#!/usr/bin/env python3
"""Auto-fill missing marketing content in spreadsheet tabs.

Scope (configurable per tab, defined in TABS below):
  - diamond_stock  : columns J (10, PRODUCT LINK) .. CU (99, Hashtags); the
    link columns J-W are operational and excluded from LLM generation.
  - jewellery_stock: columns J (10, PRODUCT LINK) .. CU (99, Hashtags); the
    link columns J-W are operational and excluded from LLM generation.
  - full_stock is NEVER auto-filled; only diamond_stock and jewellery_stock.
  - Nothing outside the configured range is read or written.
  - Reference data is taken from columns A-H (SR NO, STK, PICTURE, CODE,
    DETAILS, PRICE, LAB, CERTIFICATE ID.); the target headers drive what is
    generated, and every product's copy is unique/different.

Behaviour:
  - Loads data/<tab>.json (from the synced export).
  - For each row, collects empty cells inside the configured column range.
  - Generates the missing content with an LLM (OpenAI-compatible API) in one
    call per row, returning JSON.
  - Writes the updated data/<tab>.json/.csv locally.
  - With --write-back, pushes the newly filled cells back to the spreadsheet.

Usage:
  python scripts/fill_missing.py
  python scripts/fill_missing.py --tabs diamond_stock jewellery_stock
  python scripts/fill_missing.py --write-back --key <sa-key.json>
  python scripts/fill_missing.py --write-back --key <sa-key.json> --max-rows 50

Env:
  SPREADSHEET_ID      spreadsheet id (defaults to the colourdiam sheet)
  USER_LLM_API_KEY    LLM API key (LLM fills are skipped if unset)
  USER_LLM_BASE_URL   OpenAI-compatible base URL
  USER_LLM_MODEL      model name
  DAILY_MAX_ROWS      hard cap on LLM-filled rows per day across all tabs
                      (default 500; 0 disables the cap, a large value lifts it)
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SPREADSHEET_ID = "1kAD1ASXaaqrBmNHDVMYgj_cfW8pFJPEiRCY8ENutAvQ"
MAX_LLM_ROWS = 20
# Hard cap on LLM-filled rows per calendar day across ALL tabs, so the fill
# pipeline never burns the whole LLM quota even when triggered 24/7. Raised/
# lowered via the DAILY_MAX_ROWS env var (e.g. 0 disables filling entirely).
DAILY_MAX_ROWS = int(os.environ.get("DAILY_MAX_ROWS", "500"))
QUOTA_FILE = ".fill_quota.json"
LLM_TIMEOUT = 120
LLM_RETRIES = 4
# Seconds to sleep between LLM calls; rate limiting (HTTP 429) is the main
# failure mode, so keep a small inter-call delay and back off on 429s.
CALL_DELAY = 1.0
# Generate at most this many fields per LLM call. 75 fields in one call exceeds
# the model's output-token limit and the tail columns (multilingual fields)
# get silently truncated, so we chunk per-row generation into multiple calls.
FIELDS_PER_CALL = 15

TABS = {
    "diamond_stock": {
        "worksheet": "diamond stock ",
        "col_start": "J",
        "col_end": "CU",  # through Hashtags
        "state_file": "data/.fill_state_diamond.json",
        "extra_cols": [
            "french description",
            "french hashtag",
            "german description",
            "german hashtag",
            "lebenesse description",
            "lebenesse hashtag",
            "vestslavic description",
            "vestslavic hashtag",
            "danish description",
            "danish hashtag",
            "greek description",
            "greek hashtag",
            "polish description",
            "polish hahstag",
            "turkish description",
            "turkish hashtag",
            "sweden description",
            "sweden hashtag",
        ],
    },
    "jewellery_stock": {
        "worksheet": "jewellery stock ",
        "col_start": "J",
        "col_end": "CU",  # through Hashtags
        "state_file": "data/.fill_state_jewellery.json",
        "extra_cols": [
            "french description",
            "french hashtag",
            "german description",
            "german hashtag",
            "lebenesse description",
            "lebenesse hashtag",
            "vestslavic description",
            "vestslavic hashtag",
            "danish description",
            "danish hashtag",
            "greek description",
            "greek hashtag",
            "polish description",
            "polish hahstag",
            "turkish description",
            "turkish hashtag",
            "sweden description",
            "sweden hashtag",
        ],
    },
}

# Operational columns inside the fill range that must not be LLM-generated.
OPERATIONAL = {
    "Status",
    "check",
    "PRODUCT LINK",
    "image1 link",
    "image2 link",
    "image3 link",
    "image4 link",
    "image5 link",
    "image6 link",
    "image7 link",
    "image8 link",
    "video link",
    "multiple side image link",
    "multiple video link",
    "multiple model photo link",
    "multiple model video link",
}
LAST_UPDATED_FIELD = "Last Updated"


def col_to_idx(letter: str) -> int:
    """Convert an Excel column letter to a 1-based index (X -> 24)."""
    idx = 0
    for ch in letter.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def col_letter(idx: int) -> str:
    """Convert a 1-based index to an Excel column letter (24 -> X)."""
    s = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(state_file: str) -> set:
    try:
        with open(state_file, encoding="utf-8") as f:
            return set(json.load(f).get("processed_stk", []))
    except (OSError, json.JSONDecodeError):
        return set()


def save_state(state_file: str, processed: set):
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump({"processed_stk": sorted(processed)}, f)


def load_quota(out_dir: str) -> dict:
    """Daily LLM-row budget shared across all tabs: resets at UTC midnight."""
    path = os.path.join(out_dir, QUOTA_FILE)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(path, encoding="utf-8") as f:
            quota = json.load(f)
        if quota.get("date") != today:
            quota = {"date": today, "rows": 0}
    except (OSError, json.JSONDecodeError):
        quota = {"date": today, "rows": 0}
    return quota


def save_quota(out_dir: str, quota: dict):
    path = os.path.join(out_dir, QUOTA_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(quota, f)


def llm_complete(prompt: str) -> str:
    api_key = (os.environ.get("USER_LLM_API_KEY") or "").strip()
    base_url = os.environ.get("USER_LLM_BASE_URL", "").strip().rstrip("/") or "https://api.llm7.io/v1"
    model = os.environ.get("USER_LLM_MODEL", "").strip() or "gemini-3.1-flash-lite"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = json.dumps(
        {
            "model": model,
            "temperature": 0.7,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a luxury natural-diamond e-commerce copywriter for "
                        "ColourDiam. Output ONLY valid JSON, no markdown, no commentary."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")

    last_err = None
    for attempt in range(LLM_RETRIES):
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                time.sleep(min(2 ** attempt * CALL_DELAY, 30))
                continue
            raise
        except (urllib.error.URLError, OSError) as e:
            last_err = e
            time.sleep(CALL_DELAY)
    raise last_err


def extract_carat(product: dict) -> str:
    """Best-effort carat weight from DETAILS or PRICE (e.g. '0.377', '1.01')."""
    import re
    detail = str(product.get("DETAILS", "") or "")
    price = str(product.get("PRICE", "") or "")
    # keyed format: "Weight - 0.35" / "Weight: 0.35ct"
    m = re.search(r"(?:weight|carat|cts?)[\s:=-]+(\d+(?:\.\d+)?)", detail, re.IGNORECASE)
    if m:
        return m.group(1)
    for text in (detail, price):
        # "0.377 ct" / "1.01 cts" / "0.35 carat"
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ct|carat|cts)\b", text, re.IGNORECASE)
        if m:
            return m.group(1)
        # bare weight at start of a details line, e.g. "0.377 – Fancy Pink"
        m = re.search(r"^\s*(\d+\.\d{1,3})\b", text, re.MULTILINE)
        if m:
            return m.group(1)
    return ""


def generate_fields(prompt_fields: list, product: dict) -> dict:
    facts = {
        k: product.get(k, "")
        for k in ("STK", "check", "CODE", "DETAILS", "PRICE", "LAB", "CERTIFICATE ID.", "PICTURE")
    }
    carat = extract_carat(product)
    if carat:
        facts["CARAT"] = carat
    single_name = len(prompt_fields) == 1 and prompt_fields[0] in ("PRODUCT NAME", "PRODUCT DESCRIPTION")
    if single_name:
        rules = (
            "Rules: produce a single, concise product name/title under 60 characters "
            "and nothing else, e.g. '1.01ct D VS1 GIA Certified 18k White Gold Diamond Ring'. "
            "No SEO labels, no punctuation labels, no extra fields."
        )
    else:
        rules = (
            "Rules: SEO titles under 60 chars; meta descriptions 150-160 chars; "
            "descriptions 2-4 sentences; captions suitable for social media; mention "
            "GIA/AGL certification and free worldwide shipping where natural; "
            "multilingual fields must be translated, not transliterated; hashtags are "
            "comma-separated and on-brand. Always include the carat weight (CARAT fact) "
            "in every piece of copy where a weight is referenced - never write an empty "
            "or missing weight. CRITICAL: every field you generate must be unique to this "
            "specific product and clearly different from generic copy - derive it from the "
            "facts below (colour, shape, clarity, weight, stone type, jewellery type), "
            "never reuse boilerplate between products."
        )
    prompt = (
        "Product facts:\n"
        + json.dumps(facts, ensure_ascii=False, indent=2)
        + "\n\nGenerate values for exactly these fields and return ONLY a JSON object "
          "with these keys:\n"
        + json.dumps(prompt_fields, ensure_ascii=False)
        + "\n\n" + rules
    )
    for attempt in range(LLM_RETRIES):
        try:
            content = llm_complete(prompt)
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end <= start:
                raise json.JSONDecodeError("no JSON object", content, 0)
            parsed = json.loads(content[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, KeyError, IndexError, urllib.error.URLError, OSError) as e:
            if attempt == LLM_RETRIES - 1:
                print(f"    LLM failed after {LLM_RETRIES} tries: {e}")
            else:
                time.sleep(1)
    return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data", help="Output directory (default: data)")
    parser.add_argument("--key", help="Service account key JSON (required for --write-back)")
    parser.add_argument("--write-back", action="store_true", help="Push filled cells to the sheet")
    parser.add_argument("--max-rows", type=int, default=MAX_LLM_ROWS, help="Max LLM rows per run")
    parser.add_argument(
        "--tabs",
        nargs="+",
        default=list(TABS),
        help=f"Tabs to fill (default: all of {list(TABS)})",
    )
    parser.add_argument(
        "--allow-free",
        action="store_true",
        help="Use the built-in free fallback LLM endpoint when no USER_LLM_* env is set",
    )
    args = parser.parse_args()

    spreadsheet_id = os.environ.get("SPREADSHEET_ID", DEFAULT_SPREADSHEET_ID)

    for tab in args.tabs:
        if tab not in TABS:
            print(f"Unknown tab {tab!r}. Valid: {list(TABS)}")
            sys.exit(2)
        fill_tab(tab, args, spreadsheet_id)

    if args.write_back:
        print("Write-back done for all requested tabs.")


def _is_empty(value) -> bool:
    s = str(value or "").strip()
    if not s:
        return True
    # Broken emoji placeholder (????) counts as empty so the LLM regenerates it.
    if "????" in s:
        return True
    return False


def fill_tab(tab: str, args: argparse.Namespace, spreadsheet_id: str):
    cfg = TABS[tab]
    col_start, col_end = cfg["col_start"], cfg["col_end"]
    state_file = str(Path(args.output) / Path(cfg["state_file"]).name)

    data_path = Path(args.output) / f"{tab}.json"
    if not data_path.exists():
        print(f"Missing {data_path}. Run sync first.")
        return

    with open(data_path, encoding="utf-8") as f:
        rows = json.load(f)

    headers = list(rows[0].keys()) if rows else []
    lo, hi = col_to_idx(col_start) - 1, col_to_idx(col_end)  # 0-based slice
    if hi > len(headers):
        print(f"Column range {col_start}..{col_end} exceeds headers ({len(headers)}).")
        return
    target_cols = list(headers[lo:hi])
    for extra in cfg.get("extra_cols", []):
        if extra in headers and extra not in target_cols:
            target_cols.append(extra)
    col_index = {name: idx for idx, name in enumerate(headers)}

    if not (os.environ.get("USER_LLM_BASE_URL") or os.environ.get("USER_LLM_API_KEY")):
        if not args.allow_free:
            print("No LLM endpoint configured (USER_LLM_BASE_URL) and no API key; nothing can be generated. Skipping.")
            return
        print("Using built-in free fallback LLM endpoint (kilo-auto/free).")

    processed = load_state(state_file)
    to_fill = []
    for idx, row in enumerate(rows):
        stk = str(row.get("STK", "")).strip()
        if not stk:
            continue
        missing = [c for c in target_cols if c not in OPERATIONAL and _is_empty(row.get(c))]
        if missing:
            to_fill.append((idx, row, stk, missing))
    print(f"[{tab}] Rows needing fill: {len(to_fill)} ({col_start}..{col_end}).")

    cells_to_update = {}
    new_processed = set(processed)
    done = 0
    quota = load_quota(args.output)
    for idx, row, stk, missing in to_fill:
        if done >= args.max_rows:
            break
        if DAILY_MAX_ROWS and quota["rows"] >= DAILY_MAX_ROWS:
            print(f"  Daily LLM quota reached ({quota['rows']}/{DAILY_MAX_ROWS} rows); "
                  f"stopping fill for today.")
            break
        print(f"  Generating for STK {stk} ({len(missing)} missing fields)...")
        # Split missing fields into chunks so each LLM call stays within the
        # output-token limit (otherwise trailing columns get truncated).
        chunks = [
            missing[i : i + FIELDS_PER_CALL]
            for i in range(0, len(missing), FIELDS_PER_CALL)
        ]
        updated = 0
        for chunk in chunks:
            generated = generate_fields(chunk, row)
            for field in chunk:
                val = str(generated.get(field, "") or "").strip()
                if val:
                    row[field] = val
                    cells_to_update[(idx, col_index[field])] = val
                    updated += 1
            if len(chunks) > 1:
                time.sleep(CALL_DELAY)
        if updated:
            new_processed.add(stk)
            row[LAST_UPDATED_FIELD] = _now_iso()
            cells_to_update[(idx, col_index[LAST_UPDATED_FIELD])] = row[LAST_UPDATED_FIELD]
        print(f"    filled {updated}/{len(missing)} fields")
        done += 1
        if updated:
            quota["rows"] += 1
            save_quota(args.output, quota)
        time.sleep(CALL_DELAY)

    if not cells_to_update:
        print(f"[{tab}] No cells to fill this run.")
        return

    save_state(state_file, new_processed)
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    header = headers
    with open(data_path.with_suffix(".csv"), "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[{tab}] Updated local {tab}.json/.csv ({len(cells_to_update)} cells).")

    if args.write_back:
        if not args.key:
            print("--write-back requires --key <service-account-key.json>")
            sys.exit(2)
        try:
            write_back(cells_to_update, args.key, spreadsheet_id, cfg["worksheet"])
        except Exception as e:
            print(f"[{tab}] WARNING: write-back failed (data saved locally): {e}")
    else:
        print(f"[{tab}] Dry run: add --write-back --key <sa-key.json> to push to the sheet.")


def write_back(cells: dict, key_path: str, spreadsheet_id: str, worksheet: str):
    """Push filled cells into the matching worksheet."""
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(key_path, scope)
    client = gspread.authorize(creds)
    sp = client.open_by_key(spreadsheet_id)
    ws = sp.worksheet(worksheet)
    updates = {}
    for (row_idx, field_idx), value in cells.items():
        # field_idx is a 0-based index into the row headers; sheet columns are 1-based
        col = field_idx + 1
        # data row idx (0-based) maps to sheet row idx+2 (header on row 1)
        updates[(row_idx + 2, col)] = value
    gspread_cells = [gspread.Cell(r, c, v) for (r, c), v in sorted(updates.items())]
    if gspread_cells:
        ws.update_cells(gspread_cells, value_input_option="USER_ENTERED")
        print(f"  Wrote back {len(gspread_cells)} cells to '{worksheet}' worksheet.")


if __name__ == "__main__":
    main()
