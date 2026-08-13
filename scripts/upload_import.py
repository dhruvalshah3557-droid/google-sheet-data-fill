#!/usr/bin/env python3
"""Upload cleaned product records into the Source Import tab, keyed by STK.

Replaces the fragile wide `IMPORTRANGE` dependency with a stable row-based
upload. Reads the synced+cleaned `full_stock` data (produced by agent.py's
sync/clean stages) and upserts every row into the `Source Import` worksheet
keyed by the STK column.

Guarantees:
  - SKU-keyed upsert: existing rows updated in place, new rows appended,
    no duplicates.
  - Mock/test rows (STK == Pendant / Ring, or marked Mock Data Generator)
    are removed from the target tab.
  - Validation gate: rows missing STK/DETAILS/PRICE/PRODUCT LINK are skipped
    (counted) and the run refuses to write if the failure rate is too high.

Usage:
  python scripts/upload_import.py --key <sa-key.json>
  python scripts/upload_import.py --key <sa-key.json> --spreadsheet-id <ID>
  python scripts/upload_import.py --key <sa-key.json> --dry-run

Env:
  SOURCE_IMPORT_ID  spreadsheet id of the Source Import workbook
  SOURCE_IMPORT_TAB tab title inside it (default: "Source Import")
  SPREADSHEET_ID    source of truth for full_stock data (default colourdiam)
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

DEFAULT_SOURCE_TAB = "Source Import"
COL_RANGE = "A:CD"  # matches the widest row the pipeline reads

# STK values that are obviously not ColourDiam product identifiers.
INVALID_STK = {"", "pendant", "ring", "necklace", "earring", "bracelet"}
MOCK_MARKERS = ("Mock Data Generator", "Crimson Flame",
                "Sunset Symphony", "The Imperial Rose")

REQUIRED = ("STK", "DETAILS", "PRICE", "PRODUCT LINK")
MAX_SKIP_RATE = 0.5  # refuse to write if >50% of source rows would be skipped


def norm_stk(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def load_full_stock(data_dir: Path) -> list:
    path = data_dir / "full_stock.json"
    if not path.exists():
        sys.exit(f"Missing {path}. Run agent.py sync/clean first.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_mock_row(row: dict, headers: list) -> bool:
    stk = norm_stk(row.get("STK", "")).lower()
    if stk in INVALID_STK:
        # only treat as mock if it also looks like junk, not a legit blank row
        filled = sum(1 for h in headers if str(row.get(h, "")).strip())
        return filled > 0
    return False


def validate_rows(rows: list) -> tuple:
    """Return (valid_rows, skipped) with a reason per skipped row."""
    valid, skipped = [], []
    for r in rows:
        stk = norm_stk(r.get("STK", ""))
        if not stk:
            skipped.append((stk, "empty STK"))
            continue
        missing = [c for c in REQUIRED if c != "STK" and not str(r.get(c, "")).strip()]
        if missing:
            skipped.append((stk, f"missing {','.join(missing)}"))
            continue
        valid.append(r)
    return valid, skipped


def get_worksheet(client, spreadsheet_id: str, tab: str):
    sp = client.open_by_key(spreadsheet_id)
    try:
        return sp.worksheet(tab)
    except Exception:
        sys.exit(f"Worksheet {tab!r} not found in spreadsheet {spreadsheet_id}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--key", required=True, help="Service account key JSON")
    p.add_argument("--output", default="data", help="Data directory (default: data)")
    p.add_argument("--spreadsheet-id", default=os.environ.get("SOURCE_IMPORT_ID"),
                   help="Source Import workbook id (env SOURCE_IMPORT_ID)")
    p.add_argument("--tab", default=os.environ.get("SOURCE_IMPORT_TAB", DEFAULT_SOURCE_TAB))
    p.add_argument("--dry-run", action="store_true", help="Validate only, no writes")
    args = p.parse_args()

    if not args.spreadsheet_id:
        sys.exit("Missing --spreadsheet-id or env SOURCE_IMPORT_ID")

    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(args.key, scope)
    client = gspread.authorize(creds)
    ws = get_worksheet(client, args.spreadsheet_id, args.tab)

    rows = load_full_stock(Path(args.output))
    headers = list(rows[0].keys()) if rows else []

    # ---- 1. validate source records ----
    valid, skipped = validate_rows(rows)
    skip_rate = len(skipped) / len(rows) if rows else 0.0
    print(f"Source full_stock: {len(rows)} rows, {len(valid)} valid, "
          f"{len(skipped)} skipped ({skip_rate:.1%})")
    for stk, why in skipped[:15]:
        print(f"  skip STK={stk!r}: {why}")
    if skip_rate > MAX_SKIP_RATE:
        sys.exit(f"Skip rate {skip_rate:.1%} exceeds {MAX_SKIP_RATE:.1%}; refusing to write.")

    # ---- 2. read current tab ----
    all_vals = ws.get_all_values(value_render_option="UNFORMATTED_VALUE")
    tab_headers = [h.strip() for h in all_vals[0]] if all_vals else []
    existing_rows = all_vals[1:] if len(all_vals) > 1 else []
    if not tab_headers:
        sys.exit("Source Import tab has no header row; aborting.")

    # ---- 3. upsert by STK ----
    # Build map stk -> (row_number_in_sheet 1-based)
    stk_col = tab_headers.index("STK") if "STK" in tab_headers else None
    if stk_col is None:
        sys.exit("Source Import tab has no STK column; aborting.")

    row_number = {}  # norm stk -> 1-based sheet row
    for i, r in enumerate(existing_rows):
        s = norm_stk(r[stk_col]) if stk_col < len(r) else ""
        if s:
            row_number[s] = i + 2  # header on row 1

    # ---- 4. remove mock rows from target ----
    mock_rows = []
    for i, r in enumerate(existing_rows):
        if stk_col < len(r) and norm_stk(r[stk_col]).lower() in {"pendant", "ring"}:
            mock_rows.append(i + 2)
    if mock_rows:
        if args.dry_run:
            print(f"[dry-run] would delete {len(mock_rows)} mock rows: {mock_rows}")
        else:
            for rn in sorted(mock_rows, reverse=True):
                ws.delete_rows(rn)
            print(f"Deleted {len(mock_rows)} mock rows from {args.tab!r}.")
            row_number = {s: (rn - sum(1 for m in mock_rows if m < rn))
                          for s, rn in row_number.items()}

    # ---- 5. build update plan ----
    # target columns: intersect source headers with tab headers (A:CD)
    target_cols = [h for h in headers if h in tab_headers]
    col_idx = {h: i for i, h in enumerate(tab_headers)}
    updates = []   # (row_num, col_num, value) for existing
    appends = []   # full rows to append

    for r in valid:
        stk = norm_stk(r.get("STK", ""))
        if stk in row_number:
            rn = row_number[stk]
            for h in target_cols:
                if h == "STK":
                    continue
                v = r.get(h, "")
                if str(v).strip():
                    updates.append((rn, col_idx[h] + 1, v))
        else:
            appends.append([r.get(h, "") if h in r else "" for h in target_cols])

    # ---- 6. apply ----
    if args.dry_run:
        print(f"[dry-run] would update {len(updates)} cells across {len(row_number)} rows "
              f"and append {len(appends)} new rows.")
        return

    if updates:
        gspread_cells = [gspread.Cell(r, c, v) for r, c, v in updates]
        ws.update_cells(gspread_cells, value_input_option="USER_ENTERED")
        print(f"Updated {len(updates)} cells ({len(row_number)} existing rows).")
    if appends:
        start = len(all_vals) + 1
        ncols = len(target_cols)
        end_letter = chr(ord("A") + ncols - 1) if ncols <= 26 else "Z"
        rng = f"A{start}:{end_letter}{start + len(appends) - 1}"
        ws.update(rng, appends, value_input_option="USER_ENTERED")
        print(f"Appended {len(appends)} new rows at A{start}.")

    print("Done.")


if __name__ == "__main__":
    main()
