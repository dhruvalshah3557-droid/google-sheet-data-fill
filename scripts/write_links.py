#!/usr/bin/env python3
"""Write corrected product/model links back to the Google Spreadsheet.

Recomputes the correct PRODUCT LINK and model-media values from the synced
data files and pushes only the changed cells back to the matching worksheet.

Scope:
  - PRODUCT LINK (col J)          : correct jewellery / diamond detail URLs
  - multiple model photo link (V) : model photos from Model_Media_FTP
  - multiple model video link (W) : model videos from Model_Media_FTP
  - check / image1..8 / video / multiple side/video links (auto fetch tab
    only): replaces #N/A diamond rows with diamond_stock values

Usage:
  python scripts/write_links.py --key <sa-key.json>
"""
import argparse
import json
import sys
from urllib.parse import urlsplit, unquote
from pathlib import Path

DEFAULT_SPREADSHEET_ID = "1kAD1ASXaaqrBmNHDVMYgj_cfW8pFJPEiRCY8ENutAvQ"

# data file -> (worksheet title, columns to reconcile)
TARGETS = {
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
        [
            "check", "PRODUCT LINK",
            "image1 link", "image2 link", "image3 link", "image4 link",
            "image5 link", "image6 link", "image7 link", "image8 link",
            "video link", "multiple side image link", "multiple video link",
            "multiple model photo link", "multiple model video link",
        ],
    ),
}

LINK_COLUMNS = [
    "check", "PRODUCT LINK",
    "image1 link", "image2 link", "image3 link", "image4 link",
    "image5 link", "image6 link", "image7 link", "image8 link",
    "video link", "multiple side image link", "multiple video link",
    "multiple model photo link", "multiple model video link",
]

MODEL_MEDIA_BASE = "https://colourdiam.com/Product/Model%20Photo%20Video/"
MODEL_COLUMNS = [
    "model image link 1", "model image link 2", "model image link 3",
    "multiple model photo link", "model video link 1", "model video link 2",
    "model video link 3", "multiple model video link",
]


def model_links(value):
    """Accept only files from the canonical model-media folder; never guess names."""
    urls = []
    for url in str(value or "").splitlines():
        url = url.strip()
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.hostname in
                ("colourdiam.com", "www.colourdiam.com") and
                unquote(parsed.path).startswith("/Product/Model Photo Video/")
                and url not in urls):
            urls.append(url)
    return urls


def model_values(source):
    source = source or {}
    photos = model_links(source.get("Model Photo Links"))
    videos = model_links(source.get("Model Video Links"))
    values = photos[:3] + [""] * max(0, 3 - len(photos))
    values += ["\n".join(photos)]
    values += videos[:3] + [""] * max(0, 3 - len(videos))
    values += ["\n".join(videos)]
    return dict(zip(MODEL_COLUMNS, values))


def write_sparse_cells(ws, cells):
    """Preserve formula-owned columns and update only explicitly selected cells.

    gspread.update_cells writes the enclosing rectangle, including blanks between
    selected cells. Contiguous one-row ranges avoid clearing unrelated data.
    """
    if not cells:
        return 0
    from gspread.utils import rowcol_to_a1
    top = ws.get("A1:AC2", value_render_option="FORMULA")
    anchors = top[1] if len(top) > 1 else []
    if (ws.title.strip() == "auto fetch link from ftp" and anchors and
            str(anchors[0]).startswith("=")):
        print("  preserving formula-owned auto-fetch sheet")
        return 0
    array_columns = set()
    for i, value in enumerate(anchors):
        upper = str(value).upper()
        if upper.startswith("=") and any(fn + "(" in upper for fn in
                ("MAP", "ARRAYFORMULA", "VSTACK", "FILTER", "IMPORTRANGE")):
            array_columns.add(i + 1)
    # Stock model columns are maintained by live Model Media FTP lookups.
    if ws.title.strip() in ("diamond stock", "jewellery stock"):
        array_columns.update(range(22, 30))
    selected = {(r, c): v for r, c, v in cells if c not in array_columns}
    ranges = []
    for (row, col), value in sorted(selected.items()):
        if ranges and ranges[-1]["row"] == row and ranges[-1]["end"] + 1 == col:
            ranges[-1]["end"] = col
            ranges[-1]["values"][0].append(value)
        else:
            ranges.append({"row": row, "start": col, "end": col, "values": [[value]]})
    updates = [{"range": rowcol_to_a1(x["row"], x["start"]) + ":" +
                rowcol_to_a1(x["row"], x["end"]), "values": x["values"]}
               for x in ranges]
    for i in range(0, len(updates), 500):
        ws.batch_update(updates[i:i + 500], value_input_option="USER_ENTERED")
    return len(selected)


def norm_stk(v):
    if v is None:
        return ""
    s = str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def norm_stk_compact(v):
    return norm_stk(v).replace("\n", "_")


def jewellery_link(stk):
    s = norm_stk(stk)
    if not s:
        return ""
    parts = [p for p in s.replace("\n", "_").split("_") if p]
    if len(parts) == 1 and parts[0].isdigit():
        return f"https://colourdiam.com/productdetail/{parts[0]}"
    return "https://colourdiam.com/productdetail/Menu/" + "/".join(parts)


def build_mmf_lookup(data_dir):
    mmf_path = data_dir / "Model_Media_FTP.json"
    if not mmf_path.exists():
        return {}
    with open(mmf_path, encoding="utf-8") as f:
        mmf = json.load(f)
    lookup = {}
    for r in mmf:
        for key in (r.get("Stock ID"), r.get("FTP Folder")):
            k = norm_stk_compact(key)
            if k:
                lookup.setdefault(k, r)
    return lookup


def build_diamond_lookup(data_dir):
    path = data_dir / "diamond_stock.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        dia = json.load(f)
    return {norm_stk_compact(r.get("STK")): r for r in dia}


def recompute_row(r, mmf_lookup, diamond_lookup):
    """Return dict of column -> corrected value for a synced row."""
    out = {}
    stk = r.get("STK")
    stk_key = norm_stk_compact(stk)
    pl = str(r.get("PRODUCT LINK", "")).strip()
    is_jewellery = "productdetail" in pl or (
        "diamonddetails" not in pl and stk_key in mmf_lookup
    )
    if is_jewellery:
        link = jewellery_link(stk)
        if link and link != pl:
            out["PRODUCT LINK"] = link
        out.update(model_values(mmf_lookup.get(stk_key)))
    elif "diamonddetails" in pl:
        # diamond row: keep existing; nothing to fix here
        pass
    else:
        # #N/A or blank product link: try to restore from diamond_stock
        src = diamond_lookup.get(stk_key)
        if src:
            for c in LINK_COLUMNS:
                if "N/A" in str(r.get(c, "")):
                    out[c] = str(src.get(c, "") or "").strip()
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True, help="Service account key JSON")
    parser.add_argument("--output", default="data", help="Data directory (default: data)")
    args = parser.parse_args()

    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    data_dir = Path(args.output)
    spreadsheet_id = DEFAULT_SPREADSHEET_ID

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(args.key, scope)
    client = gspread.authorize(creds)
    sp = client.open_by_key(spreadsheet_id)

    mmf_lookup = build_mmf_lookup(data_dir)
    diamond_lookup = build_diamond_lookup(data_dir)

    for base, (tab_title, cols) in TARGETS.items():
        path = data_dir / f"{base}.json"
        if not path.exists():
            print(f"  {base}: missing {path}, skipping")
            continue
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        headers = list(rows[0].keys()) if rows else []
        col_index = {h: i for i, h in enumerate(headers)}
        missing_cols = [c for c in cols if c not in col_index]
        if missing_cols:
            print(f"  {base}: missing columns {missing_cols}, skipping")
            continue

        ws = sp.worksheet(tab_title)
        cells = []
        for row_idx, r in enumerate(rows):
            stk_key = norm_stk_compact(r.get("STK"))
            if not stk_key:
                continue
            updates = recompute_row(r, mmf_lookup, diamond_lookup)
            if not updates:
                continue
            for c in cols:
                val = updates.get(c)
                if val is None:
                    continue
                # sheet rows: header on row 1, data starts at row 2
                sheet_row = row_idx + 2
                sheet_col = col_index[c] + 1
                cells.append((sheet_row, sheet_col, val))

        if not cells:
            print(f"  {tab_title!r}: no changes")
            continue
        written = write_sparse_cells(ws, cells)
        print(f"  {tab_title!r}: wrote {written} cells")

    print("Done.")


if __name__ == "__main__":
    main()
