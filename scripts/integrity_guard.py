#!/usr/bin/env python3
"""Hourly header-aware audit and conservative repair of keyed stock records."""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit, unquote

SOURCE_HEADERS = {'stk','sr no','picture','code','details','price','lab','certificate id','certificate id.','colour','color','clarity','carat','weight','shape','metal colour','metal color'}
ERRORS = {'#REF!','#N/A','#VALUE!','#DIV/0!','#ERROR!','#NUM!','#NAME?'}

def header(value):
    return ' '.join(str(value).strip().lower().split())

def key(value):
    text = str(value or '').strip()
    if text.endswith('.0') and text[:-2].isdigit(): text = text[:-2]
    return re.sub(r'[\s_]+', '_', text)

def is_marketing_header(value):
    h = header(value)
    if h in SOURCE_HEADERS or 'link' in h or 'url' in h: return False
    return any(word in h for word in ('description','hashtag','hahstag','caption','seo','meta description','product name','tagline')) or h in {'title','hashtags'}

def select_safe_updates(title, snapshot, live, cells, only_empty=False):
    if not live or not snapshot: return []
    old_headers = list(snapshot[0]); new_headers = [str(x).strip() for x in live[0]]
    if len(set(h for h in new_headers if h)) != len([h for h in new_headers if h]): raise ValueError('Duplicate live headers; repair stopped')
    if 'STK' not in new_headers: raise ValueError('Missing STK header')
    ki = new_headers.index('STK'); counts = Counter(key(r.get('STK')) for r in snapshot)
    locations = defaultdict(list)
    for ri, row in enumerate(live[1:], 2):
        if ki < len(row): locations[key(row[ki])].append(ri)
    formula_columns = {ci for row in live[:2] for ci, value in enumerate(row)
                       if str(value).startswith('=') and any(x in str(value).upper() for x in ('ARRAYFORMULA(', 'FILTER(', 'MAP(', 'IMPORTRANGE(', 'VSTACK('))}
    # Formula-maintained media/source columns must never be replaced by snapshots.
    if title.strip() == 'auto fetch link from ftp': return []
    updates = []
    for old_row, old_col, value in cells:
        if old_row < 2 or old_row - 2 >= len(snapshot) or old_col - 1 >= len(old_headers): continue
        source = snapshot[old_row - 2]; stk = key(source.get('STK')); name = old_headers[old_col - 1]
        if not stk or counts[stk] != 1 or len(locations[stk]) != 1 or name not in new_headers: continue
        ci = new_headers.index(name); ri = locations[stk][0]; h = header(name)
        if h in SOURCE_HEADERS or h == 'check' or ci in formula_columns: continue
        if 'model' in h and 'link' in h: continue
        current = live[ri-1][ci] if ci < len(live[ri-1]) else ''
        if str(current).startswith('='): continue
        if only_empty and str(current).strip(): continue
        if str(current) == str(value): continue
        updates.append((ri, ci+1, value))
    return updates

def audit(title, values):
    if not values: return [{'tab':title,'issue':'empty worksheet'}]
    headers = [str(h).strip() for h in values[0]]; findings = []
    if 'STK' not in headers: return [{'tab':title,'issue':'missing STK header'}]
    duplicate_headers = [h for h,c in Counter(headers).items() if c>1 and h]
    if duplicate_headers: findings.append({'tab':title,'issue':'duplicate headers','headers':duplicate_headers})
    ki=headers.index('STK'); seen=defaultdict(list); copy=defaultdict(list)
    for ri,row in enumerate(values[1:],2):
        stk=key(row[ki] if ki<len(row) else '')
        if not stk: continue
        seen[stk].append(ri)
        for ci,h in enumerate(headers):
            value=str(row[ci] if ci<len(row) else '').strip()
            item={'tab':title,'row':ri,'stock':stk,'header':h}
            if value in ERRORS: findings.append({**item,'issue':'spreadsheet error'})
            hnorm = header(h)
            if hnorm in ('metal colour', 'metal color') and re.fullmatch(r'(?i)\s*(?:18|14|22|24)\s*k(?:t)?\s*', value):
                findings.append({**item,'issue':'metal purity stored as colour'})
            if any(term in hnorm for term in ('gia information', 'shipping', 'packaging')):
                if re.match(r'^\d{4}-\d{2}-\d{2}(?:T| )\d{2}:', value) or value.startswith('#'):
                    findings.append({**item,'issue':'content does not match header'})
            if is_marketing_header(h):
                if not value: findings.append({**item,'issue':'missing marketing field'})
                else:
                    normalized=re.sub(r'\d+(?:\.\d+)?','<n>',value.lower())
                    if 'hashtag' not in header(h) and 'hahstag' not in header(h): copy[(h,normalized)].append(item)
                    script={'kannada':(0x0c80,0x0cff),'telugu':(0x0c00,0x0c7f),'telagu':(0x0c00,0x0c7f),'malayalam':(0x0d00,0x0d7f)}
                    for lang,(a,b) in script.items():
                        if lang in header(h) and not any(a<=ord(ch)<=b for ch in value): findings.append({**item,'issue':'regional script missing'})
            if 'link' in header(h) and value:
                for url in re.findall(r'https?://[^\s,]+',value):
                    path=unquote(urlsplit(url).path); segments=path.split('/')
                    if '/Product/' in path and any(s.isdigit() or re.fullmatch(r'\d+(?:_\d+)+',s) for s in segments):
                        folders=[s for s in segments if s.isdigit() or re.fullmatch(r'\d+(?:_\d+)+',s)]
                        if folders and key(folders[0]) != stk: findings.append({**item,'issue':'media stock mismatch','media_stock':folders[0]})
    for stk,rows in seen.items():
        if len(rows)>1: findings.append({'tab':title,'stock':stk,'rows':rows,'issue':'duplicate stock key'})
    for items in copy.values():
        if len(items)>1:
            findings.append({'tab':title,'header':items[0]['header'],'issue':'repeated description structure','stocks':[i['stock'] for i in items]})
    return findings

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--key',required=True);ap.add_argument('--output',default='data');ap.add_argument('--repair',action='store_true');args=ap.parse_args()
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    from sync_sheet import DEFAULT_SPREADSHEET_ID
    client=gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(args.key,['https://www.googleapis.com/auth/spreadsheets']))
    sp=client.open_by_key(DEFAULT_SPREADSHEET_ID);findings=[];repairs=0
    for ws in sp.worksheets():
        if ws.title.strip() not in ('diamond stock','jewellery stock','jewelry stock'): continue
        values=ws.get_all_values(); findings.extend(audit(ws.title,values))
        if args.repair:
            formulas=ws.get_all_values(value_render_option='FORMULA');headers=values[0];updates=[]
            # Fix accidental exterior whitespace in URLs only. Facts and copy are protected.
            for ri,row in enumerate(formulas[1:],2):
                for ci,value in enumerate(row):
                    if ci<len(headers) and 'link' in header(headers[ci]) and 'model' not in header(headers[ci]) and str(value).startswith(('http',' https://',' http://')) and str(value)!=str(value).strip():
                        from gspread.utils import rowcol_to_a1
                        updates.append({'range':rowcol_to_a1(ri,ci+1),'values':[[str(value).strip()]]})
            if updates: ws.batch_update(updates,value_input_option='RAW');repairs+=len(updates)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    report={'findings':findings,'safe_repairs':repairs,'total_findings':len(findings)}
    (out/'integrity_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(f'Header-aware integrity audit: {len(findings)} findings, {repairs} safe repairs')
if __name__=='__main__':main()
