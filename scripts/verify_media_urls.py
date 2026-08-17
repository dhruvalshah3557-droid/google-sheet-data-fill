#!/usr/bin/env python3
"""Verify all media URLs in diamond_stock / jewellery_stock JSON work (HTTP < 400).

Scans the media columns, HEAD-checks every URL (GET fallback), and reports
which URLs are broken per STK. Does not modify data.

Usage:
  python scripts/verify_media_urls.py --output data [--workers 16] [--limit 100]
"""
import argparse
import concurrent.futures as cf
import json
from pathlib import Path
import urllib.request
import urllib.parse

MEDIA_COLUMNS = [
    "image1 link", "image2 link", "image3 link", "image4 link",
    "image5 link", "image6 link", "image7 link", "image8 link",
    "video link", "multiple side image link", "multiple video link",
    "multiple model photo link", "multiple model video link",
]


def _enc(url):
    return urllib.parse.quote(url, safe=":/%?&=#-._~")


def ok(url):
    url = _enc(url)
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status < 400
    except Exception:
        try:
            req = urllib.request.Request(url,
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status < 400
        except Exception:
            return False


def verify_tab(base, out, workers=16, limit=0):
    """Verify all media URLs in one tab. Returns (num_checked, broken_urls_list)."""
    out = Path(out)
    path = out / f"{base}.json"
    if not path.exists():
        print(f"missing {path}")
        return 0, []
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    rows = rows[:limit] if limit else rows
    urls = set()
    owner = {}
    for r in rows:
        for c in MEDIA_COLUMNS:
            v = str(r.get(c, "")).strip()
            if not v:
                continue
            for u in v.splitlines():
                u = u.strip()
                if not u:
                    continue
                urls.add(u)
                owner.setdefault(u, []).append((r.get("STK"), c))
    urls = list(urls)
    print(f"{base}: verifying {len(urls)} unique URLs across {len(rows)} rows")
    broken = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for u, is_ok in zip(urls, ex.map(ok, urls)):
            if not is_ok:
                broken.append(u)
    print(f"  broken: {len(broken)}/{len(urls)}")
    for u in broken[:50]:
        stks = ",".join(f"{s}" for s, _ in owner.get(u, [])[:3])
        print(f"    [{stks}] {u}")
    with open(out / f"{base}_broken_urls.txt", "w") as f:
        for u in broken:
            f.write(u + "\n")
    return len(urls), broken


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tabs", nargs="+", default=["diamond_stock", "jewellery_stock"])
    args = ap.parse_args()

    for base in args.tabs:
        verify_tab(base, args.output, workers=args.workers, limit=args.limit)


if __name__ == "__main__":
    main()
