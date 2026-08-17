#!/usr/bin/env python3
"""Fetch media links for diamond/jewellery products from the colourdiam website.

For each product in diamond_stock / jewellery_stock, loads the product detail
page, extracts every gallery item (images, videos, model photos/videos), and
writes the verified (HTTP 200) URLs into the media columns:

  - image1..image8 link       : product images (non-model), up to 8
  - video link                : first product video
  - multiple side image link  : all non-model product images (newline joined)
  - multiple video link       : all non-model product videos (newline joined)
  - multiple model photo link : model images (newline joined)
  - multiple model video link : model videos (newline joined)

Only existing columns are updated; other columns are left untouched. Links are
verified with a HEAD (GET fallback) request and only working URLs are written.

Usage:
  python scripts/fetch_media.py --output data [--workers 8] [--limit 50]
  python scripts/fetch_media.py --output data --write-back --key <sa-key.json>
"""
import argparse
import concurrent.futures as cf
import json
import re
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

BASE = "https://colourdiam.com"
MEDIA_COLUMNS = [
    "image1 link", "image2 link", "image3 link", "image4 link",
    "image5 link", "image6 link", "image7 link", "image8 link",
    "video link", "multiple side image link", "multiple video link",
    "multiple model photo link", "multiple model video link",
]
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
VID_EXT = (".mp4", ".webm", ".mov", ".m4v", ".ogg")

TARGETS = {
    "diamond_stock": "diamonddetails/Menu",
    "jewellery_stock": "productdetail",
}


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def head_ok(url):
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status < 400
    except Exception:
        try:
            status, _, _ = get(url)
            return status < 400
        except Exception:
            return False


def build_page_url(stk, kind):
    s = str(stk).strip()
    if kind == "diamond_stock":
        return f"{BASE}/diamonddetails/Menu/{s}"
    parts = [p for p in s.replace("\n", "_").split("_") if p]
    if len(parts) == 1 and parts[0].isdigit():
        return f"{BASE}/productdetail/{parts[0]}"
    return f"{BASE}/productdetail/Menu/" + "/".join(parts)


def parse_gallery(html):
    """Return ordered list of media paths from the product page gallery."""
    slider = re.search(r"id=[\"']?imgSlider[\"']?.(.*?)(?:\n\s*</div>\s*</div>)", html, re.S)
    if not slider:
        slider = re.search(r"id=[\"']?imgSlider[\"']?(.*)", html, re.S)
    if not slider:
        return []
    block = slider.group(1)
    items = []
    for m in re.finditer(r"<(?:video|img|iframe)[^>]+(?:src|data-thumb)=\"([^\"]+)\"", block):
        u = m.group(1)
        if u.startswith("/assets/") or u.startswith("assets/") or "/icon/" in u:
            continue
        if u.lower().startswith("http"):
            items.append(u)
        else:
            items.append(BASE + u if u.startswith("/") else BASE + "/" + u)
    seen = set()
    out = []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def is_model(url):
    return "/Model images/" in url or "/Model%20images/" in url


def classify(items):
    product_imgs, product_vids, model_imgs, model_vids = [], [], [], []
    for u in items:
        low = u.lower()
        is_img = low.endswith(IMG_EXT)
        is_vid = low.endswith(VID_EXT)
        if not (is_img or is_vid):
            continue
        if is_model(u):
            (model_imgs if is_img else model_vids).append(u)
        else:
            (product_imgs if is_img else product_vids).append(u)
    return product_imgs, product_vids, model_imgs, model_vids


def fetch_product(stk, kind, existing=None, verify=False):
    """Return (updates_dict, problems) for one product.

    existing: optional dict of the current row, used to preserve a working
              image1 link and to slot the rest of the gallery into image2..8.
    """
    existing = existing or {}
    page = build_page_url(stk, kind)
    try:
        status, _, raw = get(page)
        html = raw.decode("utf-8", "ignore")
        if status >= 400 or "Product Not Found" in html:
            return {}, [f"page {status}"]
    except Exception as e:
        return {}, [f"page err: {str(e)[:40]}"]
    items = parse_gallery(html)
    if not items:
        return {}, ["no gallery"]
    product_imgs, product_vids, model_imgs, model_vids = classify(items)

    def as_https(u):
        u = u.replace("https://www.colourdiam.com", BASE).replace("http://", "https://")
        return urllib.parse.quote(u, safe=":/%?&=#-._~")

    product_imgs = [as_https(u) for u in product_imgs]
    product_vids = [as_https(u) for u in product_vids]
    model_imgs = [as_https(u) for u in model_imgs]
    model_vids = [as_https(u) for u in model_vids]

    def drop_broken(urls):
        if not verify:
            return urls
        return [u for u in urls if head_ok(u)]

    product_imgs = drop_broken(product_imgs)
    product_vids = drop_broken(product_vids)
    model_imgs = drop_broken(model_imgs)
    model_vids = drop_broken(model_vids)

    updates = {}
    problems = []
    # image1: preserve existing value unchanged; only fill if empty
    cur_img1 = str(existing.get("image1 link", "")).strip()
    cur_norm = as_https(cur_img1) if cur_img1 else ""
    ordered = [u for u in product_imgs if u != cur_norm]
    for idx in range(8):
        col = f"image{idx + 1} link"
        if idx == 0 and cur_img1:
            updates[col] = cur_img1
        else:
            src = [cur_img1] + ordered if cur_img1 else ordered
            updates[col] = src[idx] if idx < len(src) else ""
    # video link = first product video
    updates["video link"] = product_vids[0] if product_vids else ""
    # multiple side image link = all non-model product images
    updates["multiple side image link"] = "\n".join(product_imgs)
    # multiple video link = all non-model product videos
    updates["multiple video link"] = "\n".join(product_vids)
    # model media
    updates["multiple model photo link"] = "\n".join(model_imgs)
    updates["multiple model video link"] = "\n".join(model_vids)

    return updates, problems


def process_tab(base, out, workers=8, skip_verify=False, limit=0):
    """Fetch media for one tab, update the JSON/CSV in place. Returns row count."""
    out = Path(out)
    path = out / f"{base}.json"
    if not path.exists():
        print(f"missing {path}, skipping")
        return 0
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    rows = rows[:limit] if limit else rows
    print(f"{base}: processing {len(rows)} rows")
    total = len(rows)
    done = 0
    fails = 0
    skipped = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {}
        for r in rows:
            stk = str(r.get("STK", "")).strip()
            if not stk:
                skipped += 1
                continue
            futures[ex.submit(fetch_product, stk, base, r, skip_verify is False)] = r
        for fut in cf.as_completed(futures):
            r = futures[fut]
            try:
                updates, problems = fut.result()
            except Exception as e:
                updates, problems = {}, [str(e)[:50]]
            if problems:
                fails += 1
            if updates:
                for col in MEDIA_COLUMNS:
                    if col in updates and col in r:
                        r[col] = updates[col]
            done += 1
            if done % 50 == 0 or done == total:
                print(f"  {base}: {done}/{total} done, {fails} failed ({skipped} no STK)")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    with open(path.with_suffix(".csv"), "w", encoding="utf-8", newline="") as f:
        import csv as _csv
        headers = list(rows[0].keys()) if rows else []
        w = _csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)
    print(f"  {base}: saved {path.name} and {path.stem}.csv")
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0, help="max products per tab (0 = all)")
    parser.add_argument("--tabs", nargs="+", default=list(TARGETS))
    parser.add_argument("--write-back", action="store_true")
    parser.add_argument("--key", help="Service account key (for --write-back)")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Do not HTTP-verify discovered URLs")
    args = parser.parse_args()

    out = Path(args.output)
    for base in args.tabs:
        if base not in TARGETS:
            print(f"unknown tab {base!r}, skipping")
            continue
        process_tab(base, out, workers=args.workers,
                    skip_verify=args.skip_verify, limit=args.limit)

    if args.write_back:
        if not args.key:
            print("--write-back requires --key <sa-key.json>")
            sys.exit(1)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from write_links import norm_stk_compact
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(args.key, scope)
        client = gspread.authorize(creds)
        sp = client.open_by_key("1kAD1ASXaaqrBmNHDVMYgj_cfW8pFJPEiRCY8ENutAvQ")

        for base in args.tabs:
            path = out / f"{base}.json"
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            headers = list(rows[0].keys())
            cols_present = [c for c in MEDIA_COLUMNS if c in headers]
            if not cols_present:
                print(f"  {base}: no media columns, skipping write-back")
                continue
            title = {"diamond_stock": "diamond stock ",
                     "jewellery_stock": "jewellery stock "}.get(base)
            ws = sp.worksheet(title)
            col_index = {h: i for i, h in enumerate(headers)}
            cells = []
            for row_idx, r in enumerate(rows):
                for c in cols_present:
                    val = str(r.get(c, ""))
                    cells.append((row_idx + 2, col_index[c] + 1, val))
            # write in batches of 10000 cells
            for i in range(0, len(cells), 10000):
                batch = cells[i:i + 10000]
                ws.update_cells(
                    [gspread.Cell(r, c, v) for r, c, v in batch],
                    value_input_option="USER_ENTERED",
                )
            print(f"  {base}: wrote {len(cells)} media cells to sheet")


if __name__ == "__main__":
    main()
