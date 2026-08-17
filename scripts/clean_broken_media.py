#!/usr/bin/env python3
"""Remove broken (404) URLs from newly-filled media columns in diamond/jewellery.

Reads the *_broken_urls.txt produced by verify_media_urls.py and drops those
URLs from image2..8 / video / multiple-* columns. image1 is left untouched
(pre-existing data). image2..8 are recompacted from the filtered product image
list, and multi-line cells are filtered line by line.

Usage:
  python scripts/clean_broken_media.py --output data
"""
import argparse
import json
import sys
from pathlib import Path

IMAGE_COLS = [f"image{i} link" for i in range(1, 9)]
FILL_COLS = IMAGE_COLS[1:] + [
    "video link", "multiple side image link", "multiple video link",
    "multiple model photo link", "multiple model video link",
]
BASE = "https://colourdiam.com"


def clean_tab(base, out):
    """Remove broken URLs from filled media columns in one tab. Returns removed count."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fetch_media import fit_cell

    out = Path(out)
    path = out / f"{base}.json"
    broken_path = out / f"{base}_broken_urls.txt"
    if not path.exists() or not broken_path.exists():
        print(f"missing {path} or {broken_path}, skipping")
        return 0
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    broken = set()
    with open(broken_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                broken.add(line)
    removed = 0
    touched = 0
    for r in rows:
        cur_img1 = str(r.get("image1 link", "")).strip()
        # product images from the multi side image cell (authoritative list)
        side = [u.strip() for u in str(r.get("multiple side image link", "")).splitlines() if u.strip()]
        good_side = [u for u in side if u not in broken]
        # rebuild image1..8: image1 kept as-is, remaining from good list
        ordered = [u for u in good_side if u.replace("www.", "") != cur_img1.replace("www.", "")]
        for i in range(8):
            col = f"image{i + 1} link"
            if i == 0:
                continue
            new_val = ordered[i - 1] if i - 1 < len(ordered) else ""
            if str(r.get(col, "")) != new_val:
                removed += len(str(r.get(col, "")).splitlines()) if not new_val else 0
                r[col] = new_val
        r["multiple side image link"] = "\n".join(good_side)
        # filter every other filled multi-line column
        for col in FILL_COLS:
            if col in ("multiple side image link",):
                continue
            v = str(r.get(col, "")).strip()
            if not v:
                continue
            lines = [u.strip() for u in v.splitlines() if u.strip()]
            good = [u for u in lines if u not in broken]
            if len(good) != len(lines):
                removed += len(lines) - len(good)
                r[col] = "\n".join(good)
                touched += 1
        # enforce Google Sheets cell-size limit on every media column
        for col in FILL_COLS + [IMAGE_COLS[0]]:
            v = str(r.get(col, ""))
            if len(v) > 50000:
                r[col] = fit_cell(v)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    with open(path.with_suffix(".csv"), "w", encoding="utf-8", newline="") as f:
        import csv as _csv
        headers = list(rows[0].keys()) if rows else []
        w = _csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)
    print(f"{base}: removed {removed} broken URLs, touched {touched} cells -> saved")
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data")
    ap.add_argument("--tabs", nargs="+", default=["diamond_stock", "jewellery_stock"])
    args = ap.parse_args()

    for base in args.tabs:
        clean_tab(base, args.output)


if __name__ == "__main__":
    main()
