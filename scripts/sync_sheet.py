#!/usr/bin/env python3
"""Sync all tabs of a Google Spreadsheet to JSON + CSV files.

Usage:
  python scripts/sync_sheet.py --key <service-account-key.json> --output data

Env:
  SPREADSHEET_ID  Google Sheets spreadsheet id (default: the colourdiam stock sheet)
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone


DEFAULT_SPREADSHEET_ID = "1kAD1ASXaaqrBmNHDVMYgj_cfW8pFJPEiRCY8ENutAvQ"
MAX_ROWS = 4003
MAX_COLS = 136


def safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\- ]", "", name).strip()
    name = re.sub(r"\s+", "_", name)
    return name or "sheet"


def read_all_tabs(key_path: str, spreadsheet_id: str):
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(key_path, scope)
    client = gspread.authorize(creds)
    sp = client.open_by_key(spreadsheet_id)
    tabs = {}
    for ws in sp.worksheets():
        rows = ws.get_all_values(value_render_option="UNFORMATTED_VALUE")
        if not rows:
            continue
        header = [h.strip() for h in rows[0]]
        data = []
        for r in rows[1:]:
            row = {}
            for i, h in enumerate(header):
                v = r[i] if i < len(r) else ""
                if isinstance(v, str):
                    v = v.strip()
                row[h] = v
            data.append(row)
        tabs[ws.title] = data
    return tabs


def write_tab(out_dir: str, title: str, data: list):
    base = os.path.join(out_dir, safe_filename(title))
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    header = list(data[0].keys()) if data else []
    with open(base + ".csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True, help="Path to service account key JSON")
    parser.add_argument(
        "--output", default="data", help="Output directory for JSON/CSV files"
    )
    args = parser.parse_args()

    spreadsheet_id = os.environ.get("SPREADSHEET_ID", DEFAULT_SPREADSHEET_ID)
    os.makedirs(args.output, exist_ok=True)

    tabs = read_all_tabs(args.key, spreadsheet_id)
    if not tabs:
        print("No tabs found.")
        sys.exit(1)

    for title, data in tabs.items():
        write_tab(args.output, title, data)
        print(f"  {title!r}: {len(data)} rows")

    manifest = {
        "spreadsheet_id": spreadsheet_id,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "tabs": {title: len(data) for title, data in tabs.items()},
    }
    with open(os.path.join(args.output, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Synced {len(tabs)} tabs -> {args.output}")


if __name__ == "__main__":
    main()
