#!/usr/bin/env python3
"""Auto-fill missing marketing content in spreadsheet tabs.

Scope (configurable per tab, defined in TABS below):
  - full_stock     : columns X (24, PRODUCT DESCRIPTION) .. CT (98, Hashtags).
  - diamond_stock  : column X (PRODUCT DESCRIPTION / product name) only.
  - jewellery_stock: column X (PRODUCT DESCRIPTION / product name) only.
  - Nothing outside the configured range is read or written.

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
LLM_TIMEOUT = 120
LLM_RETRIES = 2

TABS = {
    "full_stock": {
        "worksheet": "full stock ",
        "col_start": "X",  # 24 -> PRODUCT DESCRIPTION
        "col_end": "CT",  # 98 -> Hashtags
        "state_file": "data/.fill_state.json",
    },
    "diamond_stock": {
        "worksheet": "diamond stock ",
        "col_start": "X",
        "col_end": "X",
        "state_file": "data/.fill_state_diamond.json",
    },
    "jewellery_stock": {
        "worksheet": "jewellery stock ",
        "col_start": "X",
        "col_end": "X",
        "state_file": "data/.fill_state_jewellery.json",
    },
}

# Operational columns inside the fill range that must not be LLM-generated.
OPERATIONAL = {"Status"}
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


def llm_complete(prompt: str) -> str:
    api_key = (os.environ.get("USER_LLM_API_KEY") or "").strip()
    base_url = os.environ.get("USER_LLM_BASE_URL", "").strip().rstrip("/") or "https://api.kilo.ai/api/gateway"
    model = os.environ.get("USER_LLM_MODEL", "").strip() or "kilo-auto/free"
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
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def generate_fields(prompt_fields: list, product: dict) -> dict:
    facts = {k: product.get(k, "") for k in ("STK", "check", "CODE", "DETAILS", "PRICE", "LAB")}
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
            "comma-separated and on-brand."
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
        fill_tab(tab, args)

    if args.write_back:
        print("Write-back done for all requested tabs.")


def fill_tab(tab: str, args: argparse.Namespace):
    cfg = TABS[tab]
    col_start, col_end = cfg["col_start"], cfg["col_end"]
    state_file = cfg["state_file"]

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
    target_cols = headers[lo:hi]
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
        if not stk or stk in processed:
            continue
        missing = [c for c in target_cols if c not in OPERATIONAL and not str(row.get(c, "")).strip()]
        if missing:
            to_fill.append((idx, row, stk, missing))
    print(f"[{tab}] Rows needing fill: {len(to_fill)} ({col_start}..{col_end}).")

    cells_to_update = {}
    new_processed = set(processed)
    done = 0
    for idx, row, stk, missing in to_fill:
        if done >= args.max_rows:
            break
        print(f"  Generating for STK {stk} ({len(missing)} missing fields)...")
        generated = generate_fields(missing, row)
        updated = 0
        for field in missing:
            val = str(generated.get(field, "") or "").strip()
            if val:
                row[field] = val
                cells_to_update[(idx, col_index[field])] = val
                updated += 1
        if updated:
            new_processed.add(stk)
            row[LAST_UPDATED_FIELD] = _now_iso()
            cells_to_update[(idx, col_index[LAST_UPDATED_FIELD])] = row[LAST_UPDATED_FIELD]
        print(f"    filled {updated}/{len(missing)} fields")
        done += 1
        time.sleep(0.3)

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
        write_back(cells_to_update, args.key, spreadsheet_id, cfg["worksheet"])
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
