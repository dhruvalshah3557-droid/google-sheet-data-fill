#!/usr/bin/env python3
"""ColourDiam data agent: sync -> fix links -> fill missing -> find mistakes -> commit.

Runs the full spreadsheet pipeline and reports any problems it finds:

  1. sync     : pull every tab from Google Sheets into data/ (JSON + CSV).
  2. links    : recompute correct PRODUCT LINK / model-media values, write back.
  3. clean    : mechanical data fixes (SKU separators, cert IDs as text,
                drop mock/test rows).
  4. media    : fetch + verify + clean product/media URLs, write back.
  5. fix      : auto-fix glitches found by check (error markers, broken media
                URLs, bad links) and write the fixes back to the sheet.
  6. fill     : LLM-generate missing marketing cells (diamond/jewellery names,
                and full_stock X..CT when requested) and write back.
  7. check    : audit the synced data for mistakes (empty required cells,
                #N/A links, malformed product links, duplicate/empty STK, ...).
  8. commit   : commit and push the updated data (unless --no-commit).

Every stage is optional and non-fatal by default: a failure in one stage is
reported and the agent continues, so a bad LLM run never blocks a good sync.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SPREADSHEET_ID = "1kAD1ASXaaqrBmNHDVMYgj_cfW8pFJPEiRCY8ENutAvQ"

# Columns that must never be empty / must look sane, per tab.
REQUIRED = {
    "diamond_stock": ["STK"],
    "jewellery_stock": ["STK"],
    "full_stock": ["STK"],
}
# The unique key column per tab (used for empty/duplicate checks).
KEY_COLUMN = {
    "diamond_stock": "STK",
    "jewellery_stock": "STK",
    "full_stock": "STK",
    "auto_fetch_link_from_ftp": "STK",
    "Model_Media_FTP": "Stock ID",
    "Tag_Print": "StockID",
    "pinterest": "id",
    "shopee": "Stock",
}
# Columns that must not contain spreadsheet error values.
ERROR_MARKERS = ("#N/A", "#REF!", "#VALUE!", "#DIV/0!", "#ERROR!")
LINK_SUBSTR = "http"


class Agent:
    def __init__(self, args):
        self.args = args
        self.out_dir = Path(args.output)
        self.spreadsheet_id = os.environ.get("SPREADSHEET_ID", DEFAULT_SPREADSHEET_ID)
        self.issues = []
        self.stage_log = []

    # ---------- helpers ----------
    def report(self, severity, message):
        line = f"[{severity.upper()}] {message}"
        print(line)
        self.issues.append((severity, message))

    def run_stage(self, name, fn):
        self.stage_log.append(name)
        print(f"\n=== stage: {name} ===")
        try:
            fn()
        except Exception as e:
            self.report("error", f"{name} failed: {e}")

    def load_rows(self, base):
        path = self.out_dir / f"{base}.json"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing (run sync first)")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # ---------- stage: sync ----------
    def sync(self):
        if not self.args.key:
            raise SystemExit("sync requires --key <sa-key.json>")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from sync_sheet import read_all_tabs, write_tab
        tabs = read_all_tabs(self.args.key, self.spreadsheet_id)
        if not tabs:
            raise SystemExit("no tabs returned from spreadsheet")
        for title, data in tabs.items():
            write_tab(str(self.out_dir), title, data)
        manifest = {
            "spreadsheet_id": self.spreadsheet_id,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "tabs": {t: len(d) for t, d in tabs.items()},
        }
        with open(self.out_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        print(f"  synced {len(tabs)} tabs")

    # ---------- stage: links ----------
    def links(self):
        if not self.args.key:
            raise SystemExit("links requires --key <sa-key.json>")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from write_links import (
            LINK_COLUMNS, build_diamond_lookup, build_mmf_lookup,
            norm_stk_compact, recompute_row,
        )
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        mmf = build_mmf_lookup(self.out_dir)
        dia = build_diamond_lookup(self.out_dir)
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(self.args.key, scope)
        client = gspread.authorize(creds)
        sp = client.open_by_key(self.spreadsheet_id)
        targets = {
            "full_stock": (
                "full stock ",
                ["PRODUCT LINK", "multiple model photo link", "multiple model video link"],
            ),
            "jewellery_stock": (
                "jewellery stock ",
                ["PRODUCT LINK", "multiple model photo link", "multiple model video link"],
            ),
            "auto_fetch_link_from_ftp": (
                "auto fetch link from ftp ",
                LINK_COLUMNS,
            ),
        }
        for base, (tab_title, cols) in targets.items():
            path = self.out_dir / f"{base}.json"
            if not path.exists():
                print(f"  {base}: missing, skipping")
                continue
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            headers = list(rows[0].keys()) if rows else []
            col_index = {h: i for i, h in enumerate(headers)}
            if missing := [c for c in cols if c not in col_index]:
                print(f"  {base}: missing columns {missing}, skipping")
                continue
            ws = sp.worksheet(tab_title)
            cells = []
            for row_idx, r in enumerate(rows):
                if not norm_stk_compact(r.get("STK")):
                    continue
                for c, val in recompute_row(r, mmf, dia).items():
                    if c in col_index and val:
                        cells.append((row_idx + 2, col_index[c] + 1, val))
            if not cells:
                print(f"  {tab_title!r}: no changes")
                continue
            ws.update_cells(
                [gspread.Cell(r, c, v) for r, c, v in cells],
                value_input_option="USER_ENTERED",
            )
            print(f"  {tab_title!r}: wrote {len(cells)} cells")

    # ---------- stage: fill ----------
    def fill(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from fill_missing import TABS, fill_tab

        tabs = self.args.tabs or list(TABS)
        for tab in tabs:
            if tab not in TABS:
                self.report("warn", f"unknown tab {tab!r}, skipping")
                continue
            print(f"--- filling {tab} ---")
            fill_tab(tab, self.args, self.spreadsheet_id)

    # ---------- stage: check ----------
    def check(self):
        """Audit synced data for mistakes. No spreadsheet/LLM access needed.

        Detects: missing/duplicate keys, spreadsheet error markers, malformed
        or blank product links, non-http media URLs, whitespace-only cells,
        broken media URLs (from *_broken_urls.txt), empty marketing cells, and
        cells exceeding the Google Sheets 50k-char limit.
        """
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # per-tab column requirements from the fill config + hardcoded REQUIRED
        for base in ["diamond_stock", "jewellery_stock", "full_stock",
                     "auto_fetch_link_from_ftp", "Model_Media_FTP", "Tag_Print",
                     "pinterest", "shopee"]:
            path = self.out_dir / f"{base}.json"
            if not path.exists():
                self.report("warn", f"{base}: data file missing")
                continue
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            if not rows:
                self.report("warn", f"{base}: empty (0 rows)")
                continue
            headers = list(rows[0].keys())
            if "" in headers:
                self.report("warn", f"{base}: has unnamed trailing header column")

            key_col = KEY_COLUMN.get(base, "STK")
            has_key = key_col in headers
            seen_stk = set()
            dup_stk = set()
            empty_stk = 0
            error_cells = 0
            blank_cells = 0
            bad_links = 0
            bad_media = 0
            ws_cells = 0
            broken_urls = set()
            broken_path = self.out_dir / f"{base}_broken_urls.txt"
            if broken_path.exists():
                with open(broken_path, encoding="utf-8") as f:
                    broken_urls = {ln.strip() for ln in f if ln.strip()}
            for r in rows:
                stk = str(r.get(key_col, "")).strip() if has_key else ""
                if not stk:
                    empty_stk += 1
                elif stk in seen_stk:
                    dup_stk.add(stk)
                else:
                    seen_stk.add(stk)
                for h, v in r.items():
                    if not has_key or h == key_col:
                        continue
                    if not isinstance(v, str):
                        continue
                    if any(m in v for m in ERROR_MARKERS):
                        error_cells += 1
                    if not v.strip():
                        blank_cells += 1
                    low = h.lower()
                    if low == "product link":
                        if "http" not in v or " " in v.strip():
                            bad_links += 1
                    elif "link" in low:
                        for u in v.splitlines():
                            u = u.strip()
                            if not u:
                                continue
                            if not u.lower().startswith("http"):
                                bad_media += 1
                            elif u in broken_urls:
                                bad_media += 1
                    if len(v) > 50000:
                        ws_cells += 1
            if has_key and empty_stk:
                self.report("warn", f"{base}: {empty_stk} rows with empty {key_col!r}")
            if has_key and dup_stk:
                self.report("warn", f"{base}: {len(dup_stk)} duplicate {key_col!r}s, e.g. "
                                     f"{sorted(dup_stk)[:5]}")
            if error_cells:
                self.report("error", f"{base}: {error_cells} cells contain "
                                     f"spreadsheet errors like #N/A")
            if blank_cells:
                print(f"  [info] {base}: {blank_cells} blank cells outside key column")
            if bad_links:
                self.report("warn", f"{base}: {bad_links} malformed PRODUCT LINK cells")
            if bad_media:
                self.report("warn", f"{base}: {bad_media} non-http or broken media URLs")
            if ws_cells:
                self.report("warn", f"{base}: {ws_cells} cells exceed 50k chars")

            # column X (PRODUCT NAME / PRODUCT DESCRIPTION) coverage for stock tabs
            if base in ("diamond_stock", "jewellery_stock"):
                x = headers[23] if len(headers) > 23 else None
                if x:
                    missing = sum(1 for r in rows if not str(r.get(x, "")).strip())
                    pct = 100.0 * missing / len(rows)
                    if missing:
                        self.report("warn", f"{base}: {missing}/{len(rows)} ({pct:.0f}%) "
                                            f"rows missing {x!r}")
                    else:
                        print(f"  [OK] {base}: {x!r} filled for all {len(rows)} rows")
            # full_stock X..CT coverage
            if base == "full_stock" and len(headers) >= 98:
                lo, hi = 23, 98
                empty = 0
                for r in rows:
                    for i in range(lo, hi):
                        if not str(r.get(headers[i], "")).strip():
                            empty += 1
                            break
                if empty:
                    self.report("warn", f"full_stock: {empty}/{len(rows)} rows have "
                                        f"empty marketing cells (X..CT)")
                else:
                    print("  [OK] full_stock: marketing columns X..CT filled")

    # ---------- stage: clean ----------
    def clean(self):
        """Fix known data-quality issues in the synced data files.

        Safe, mechanical fixes only (no LLM):
          - Normalize multi-item SKU separators (newlines/spaces -> '_').
          - Force CERTIFICATE ID. to text (no scientific notation).
          - Drop rows that are clearly mock/test records.
        Conflicting duplicates are REPORTED, not auto-deleted.
        """
        import re

        self.out_dir.mkdir(parents=True, exist_ok=True)

        test_markers = ("Mock Data Generator", "Crimson Flame",
                        "Sunset Symphony", "The Imperial Rose")

        for base in ["full_stock", "diamond_stock", "jewellery_stock",
                     "auto_fetch_link_from_ftp"]:
            path = self.out_dir / f"{base}.json"
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            if not rows:
                continue
            headers = list(rows[0].keys())
            if "STK" not in headers:
                continue
            stk_col = headers.index("STK")

            changed = False
            kept = []
            removed_test = 0
            for r in rows:
                # 4. drop mock/test records
                if any(str(r.get(h, "")).find(m) >= 0 for h in ("PICTURE", "CODE", "video link")
                       for m in test_markers):
                    removed_test += 1
                    changed = True
                    continue
                # 2. normalize multi-item SKU separators
                stk = str(r.get("STK", "")).strip()
                if stk:
                    norm = re.sub(r"[ \n\r\t]+", "_", stk)
                    norm = re.sub(r"_+", "_", norm)
                    if norm != stk:
                        r["STK"] = norm
                        changed = True
                # 3. force cert id to text (no sci notation)
                if "CERTIFICATE ID." in r:
                    v = r.get("CERTIFICATE ID.")
                    if isinstance(v, (int, float)) or (isinstance(v, str) and "E" in v):
                        r["CERTIFICATE ID."] = str(v)
                        changed = True
                kept.append(r)
            if removed_test:
                print(f"  {base}: removed {removed_test} mock/test rows")
            if changed:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(kept, f, indent=2, ensure_ascii=False)
                with open(path.with_suffix(".csv"), "w", encoding="utf-8", newline="") as f:
                    import csv as _csv
                    w = _csv.DictWriter(f, fieldnames=headers)
                    w.writeheader()
                    w.writerows(kept)
                print(f"  {base}: cleaned and saved (SKU normalization / cert text)")
            else:
                print(f"  {base}: nothing to clean")

    # ---------- stage: media ----------
    def media(self):
        """Fetch product media URLs from the website, verify, clean, write back.

        For diamond_stock / jewellery_stock: scrape each product page gallery,
        fill image2..8 / video / multiple-* media columns, HTTP-verify every URL,
        drop broken ones, then push the media cells back to the spreadsheet.
        Requires --key (service account).
        """
        if not self.args.key:
            raise SystemExit("media requires --key <sa-key.json>")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        from fetch_media import MEDIA_COLUMNS as MEDIA_COLS, fit_cell, process_tab
        from verify_media_urls import verify_tab
        from clean_broken_media import clean_tab

        self.out_dir.mkdir(parents=True, exist_ok=True)
        tabs = {"diamond_stock": "diamond stock ",
                "jewellery_stock": "jewellery stock "}
        # 1. fetch + 2. verify + 3. clean for every target tab
        # (fetch without inline verify for speed; verify_tab is authoritative)
        for base in tabs:
            process_tab(base, self.out_dir, workers=self.args.workers, skip_verify=True)
            verify_tab(base, self.out_dir, workers=self.args.workers)
            clean_tab(base, self.out_dir)

        # 4. write back media cells
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(self.args.key, scope)
        client = gspread.authorize(creds)
        sp = client.open_by_key(self.spreadsheet_id)
        for base, tab_title in tabs.items():
            path = self.out_dir / f"{base}.json"
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            headers = list(rows[0].keys()) if rows else []
            cols_present = [c for c in MEDIA_COLS if c in headers]
            if not cols_present:
                print(f"  {base}: no media columns, skipping write-back")
                continue
            col_index = {h: i for i, h in enumerate(headers)}
            ws = sp.worksheet(tab_title)
            cells = []
            for row_idx, r in enumerate(rows):
                for c in cols_present:
                    val = fit_cell(r.get(c, ""))
                    if not val:
                        continue
                    cells.append((row_idx + 2, col_index[c] + 1, val))
            for i in range(0, len(cells), 10000):
                batch = cells[i:i + 10000]
                ws.update_cells(
                    [gspread.Cell(r, c, v) for r, c, v in batch],
                    value_input_option="USER_ENTERED",
                )
            print(f"  {tab_title!r}: wrote {len(cells)} media cells to sheet")

    # ---------- stage: fix ----------
    def fix(self):
        """Auto-fix mechanical glitches found by the check stage.

        Safe, deterministic fixes only (no LLM):
          - Clear spreadsheet error markers (#N/A, #REF!, #VALUE!, ...) from
            non-key cells.
          - Drop broken media URLs from media columns (diamond/jewellery).
          - Recompute PRODUCT LINK / model media links via write_links.
          - Normalize multi-item SKU separators + cert-id text (reuses clean).
        Anything ambiguous is reported but left untouched.
        """
        if not self.args.key:
            raise SystemExit("fix requires --key <sa-key.json>")
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # 1. drop broken media URLs (updates diamond/jewellery JSON/CSV)
        for base in ("diamond_stock", "jewellery_stock"):
            broken_path = self.out_dir / f"{base}_broken_urls.txt"
            if not broken_path.exists():
                continue
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from clean_broken_media import clean_tab
            removed = clean_tab(base, self.out_dir)
            if removed:
                self.report("warn", f"{base}: removed {removed} broken media URLs")

        # 2. fix error markers / whitespace / links in the stock tabs
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from write_links import (
            build_diamond_lookup, build_mmf_lookup, norm_stk_compact, recompute_row,
        )
        mmf = build_mmf_lookup(self.out_dir)
        dia = build_diamond_lookup(self.out_dir)

        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(self.args.key, scope)
        client = gspread.authorize(creds)
        sp = client.open_by_key(self.spreadsheet_id)

        targets = {
            "diamond_stock": "diamond stock ",
            "jewellery_stock": "jewellery stock ",
            "full_stock": "full stock ",
            "auto_fetch_link_from_ftp": "auto fetch link from ftp ",
        }
        total_cells = 0
        for base, tab_title in targets.items():
            path = self.out_dir / f"{base}.json"
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            if not rows:
                continue
            headers = list(rows[0].keys())
            col_index = {h: i for i, h in enumerate(headers)}
            if "STK" not in col_index:
                continue

            cells = []
            for row_idx, r in enumerate(rows):
                stk_key = norm_stk_compact(r.get("STK"))
                if not stk_key:
                    continue
                # error markers / broken placeholders in any non-key cell
                for h, v in list(r.items()):
                    if h == "STK" or not isinstance(v, str):
                        continue
                    if any(m in v for m in ERROR_MARKERS) or "????" in v:
                        # leave link columns to recompute_row; blank the rest
                        if "link" not in h.lower():
                            r[h] = ""
                            cells.append((row_idx + 2, col_index[h] + 1, ""))
                # recompute links (product + model media)
                for c, val in recompute_row(r, mmf, dia).items():
                    if c in col_index and val:
                        old = str(r.get(c, "") or "").strip()
                        if old != val:
                            r[c] = val
                            cells.append((row_idx + 2, col_index[c] + 1, val))

            if not cells:
                print(f"  {base}: nothing to fix")
                continue
            ws = sp.worksheet(tab_title)
            gspread_cells = [gspread.Cell(r, c, v) for r, c, v in cells]
            ws.update_cells(gspread_cells, value_input_option="USER_ENTERED")
            total_cells += len(cells)
            self.report("warn", f"{base}: auto-fixed {len(cells)} cells in sheet")

            # persist local JSON/CSV too
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=2, ensure_ascii=False)
            with open(path.with_suffix(".csv"), "w", encoding="utf-8", newline="") as f:
                import csv as _csv
                w = _csv.DictWriter(f, fieldnames=headers)
                w.writeheader()
                w.writerows(rows)

        print(f"  fix stage total: {total_cells} cells written back")

    # ---------- stage: commit ----------
    def commit(self):
        if self.args.no_commit:
            print("  skip commit (--no-commit)")
            return
        out = subprocess.run(
            ["git", "add", "data"], cwd=str(Path(__file__).resolve().parent.parent)
        )
        if out.returncode != 0:
            self.report("error", "git add data failed")
            return
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        if diff.returncode == 0:
            print("  no changes to commit")
            return
        subprocess.run(
            [
                "git", "commit", "-m",
                "chore: agent sync google sheet data",
            ],
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        if self.args.push:
            subprocess.run(["git", "push"], cwd=str(Path(__file__).resolve().parent.parent))

    # ---------- driver ----------
    def run(self):
        for stage in self.args.stages:
            if stage == "sync":
                self.run_stage("sync", self.sync)
            elif stage == "links":
                self.run_stage("links", self.links)
            elif stage == "media":
                self.run_stage("media", self.media)
            elif stage == "fix":
                self.run_stage("fix", self.fix)
            elif stage == "fill":
                self.run_stage("fill", self.fill)
            elif stage == "check":
                self.run_stage("check", self.check)
            elif stage == "clean":
                self.run_stage("clean", self.clean)
            elif stage == "commit":
                self.run_stage("commit", self.commit)

        print("\n" + "=" * 50)
        if self.issues:
            errors = [m for s, m in self.issues if s == "error"]
            warns = [m for s, m in self.issues if s == "warn"]
            print(f"Agent finished. {len(errors)} error(s), {len(warns)} warning(s).")
            for s, m in self.issues:
                print(f"  {s}: {m}")
        else:
            print("Agent finished cleanly: no issues found.")


def main():
    import argparse as ap

    p = ap.ArgumentParser(description=__doc__, formatter_class=ap.RawDescriptionHelpFormatter)
    p.add_argument("--key", help="Service account key JSON (needed for sync/links/write-back)")
    p.add_argument("--output", default="data", help="Data directory (default: data)")
    p.add_argument("--stages", nargs="+", default=["sync", "links", "media", "clean", "fix", "fill", "check", "commit"],
                   help="Which stages to run (sync links media clean fix fill check commit)")
    p.add_argument("--check-only", action="store_true",
                   help="Run only the audit stage (no key required)")
    p.add_argument("--no-commit", action="store_true", help="Do not commit/push data")
    p.add_argument("--push", action="store_true", help="git push after commit (CI only)")
    p.add_argument("--tabs", nargs="+", default=None,
                   help="Tabs to fill (default: all configured in fill_missing.py)")
    p.add_argument("--max-rows", type=int, default=200,
                   help="Max LLM rows to fill per run (default 200)")
    p.add_argument("--allow-free", action="store_true",
                   help="Use built-in free fallback LLM endpoint when no USER_LLM_* set")
    p.add_argument("--workers", type=int, default=10,
                   help="Threads for media fetch/verify (default 10)")
    args = p.parse_args()

    if args.check_only:
        args.stages = ["check"]
        args.no_commit = True
        args.key = None
    args.write_back = bool(args.key)
    if not args.allow_free and not (os.environ.get("USER_LLM_BASE_URL") or os.environ.get("USER_LLM_API_KEY")):
        args.allow_free = True
        print("No LLM env configured -> using free fallback endpoint.")

    Agent(args).run()


if __name__ == "__main__":
    main()
