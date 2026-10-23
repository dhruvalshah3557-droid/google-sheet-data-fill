#!/usr/bin/env python3
"""Generate reviewed marketing tags in memory; abort on any live input drift."""
import argparse
import json
import subprocess
from pathlib import Path
from collections import defaultdict
from hashtag_quality import tokens, signature, cell_issues
from sync_sheet import DEFAULT_SPREADSHEET_ID

TABS={'jewellery stock ':739420449,'diamond stock ':1859613590}
HEADERS={47:'HASHTAGS',76:'Burmese Hashtag',78:'Thai Hashtag',80:'Filipino Hashtag',82:'Chinese Hashtag',84:'Russian Hashtag',86:'Japanese Hashtag',88:'Korean Hashtag',90:'israli hashtag',92:'arabic hashtag',94:'vietnam hashtag',96:'spanish hashtag',98:'Indonesian hashtag',100:'italian hashtag',102:'french hashtag',104:'german hashtag',106:'lebenesse hashtag',110:'danish hashtag',112:'greek hashtag',114:'polish hahstag',116:'sweden hashtag',118:'kannada hashtag',120:'telagu hashtag',122:'malayalam hashtag',124:'turkish hashtag',126:'Luxembourgish hashtag',146:'Hashtags'}

def cell(row,col):return row[col] if col<len(row) else ''

def validate(updates,snapshots):
    seen=set();sets=defaultdict(set)
    for tab,row,col,value in updates:
        if tab not in TABS or col-1 not in HEADERS:raise ValueError('unapproved destination')
        key=(tab,row,col)
        if key in seen:raise ValueError('duplicate destination')
        seen.add(key)
        formatted,formulas,raw=snapshots[tab]
        if row<2 or row>len(formatted):raise ValueError('row bounds')
        if str(cell(formatted[0],col-1)).strip()!=HEADERS[col-1].strip():raise ValueError('header drift')
        if str(cell(formulas[row-1],col-1)).startswith('='):raise ValueError('formula destination')
        tag=tokens(value)
        if not tag or len(tag)!=len(set(tag)):raise ValueError('duplicate or empty tags')
        if cell_issues(HEADERS[col-1],value):raise ValueError('tag quality')
        sig=signature(value,True)
        if sig in sets[(tab,col)]:raise ValueError('repeated product hashtag set')
        sets[(tab,col)].add(sig)
    return updates

def preflight(before,after,updates):
    for tab in TABS:
        if len(before[tab][0])!=len(after[tab][0]):raise ValueError('row count drift')
        if before[tab][0][0]!=after[tab][0][0]:raise ValueError('header drift')
        for a,b in zip(before[tab][2],after[tab][2]):
            if [cell(a,i) for i in range(9)]!=[cell(b,i) for i in range(9)]:raise ValueError('source drift')
    for tab,row,col,value in updates:
        if cell(before[tab][0][row-1],col-1)!=cell(after[tab][0][row-1],col-1):raise ValueError('old hashtag drift')
    validate(updates,after)

def ranges_for(updates):
    from gspread.utils import rowcol_to_a1
    groups=[]
    for tab,row,col,value in sorted(updates):
        if groups and groups[-1]['tab']==tab and groups[-1]['row']==row and groups[-1]['end']+1==col:
            groups[-1]['end']=col;groups[-1]['values'][0].append(value)
        else:groups.append({'tab':tab,'row':row,'start':col,'end':col,'values':[[value]]})
    return [{'range':"'"+x['tab'].replace("'","''")+"'!"+rowcol_to_a1(x['row'],x['start'])+':'+rowcol_to_a1(x['row'],x['end']),'values':x['values']} for x in groups]

def main():
    p=argparse.ArgumentParser();p.add_argument('--key',required=True);a=p.parse_args()
    import gspread
    from google.oauth2.service_account import Credentials
    credential=Credentials.from_service_account_file(a.key,scopes=['https://www.googleapis.com/auth/spreadsheets'])
    try:
        sp=gspread.authorize(credential).open_by_key(DEFAULT_SPREADSHEET_ID)
        worksheets={tab:sp.worksheet(tab) for tab in TABS}
        for tab,ws in worksheets.items():
            if ws.id!=TABS[tab]:raise ValueError('sheet ID drift')
        def snapshot():return {tab:(ws.get_all_values(),ws.get_all_values(value_render_option='FORMULA'),ws.get_all_values(value_render_option='UNFORMATTED_VALUE')) for tab,ws in worksheets.items()}
        before=snapshot()
        rows=[{'sheet':tab,'sheet_id':TABS[tab],'row':ri,'stock':str(cell(row,1)).strip(),'code':str(cell(row,3) or ''),'details':str(cell(row,4) or ''),'lab':str(cell(row,6) or ''),'cert':str(cell(row,7) or ''),'type':str(cell(row,8) or '')} for tab in TABS for ri,row in enumerate(before[tab][2][1:],2) if str(cell(row,1)).strip()]
        if any(str(cell(before[tab][0][0],1)).strip()!='STK' for tab in TABS):raise ValueError('stock header drift')
        process=subprocess.run(['node',str(Path(__file__).with_name('generate_live_hashtags.cjs')),str(Path(__file__).with_name('hashtag_vocabulary.json'))],input=json.dumps({'rows':rows},ensure_ascii=False),text=True,capture_output=True)
        if process.returncode:raise ValueError('generator failed')
        result=json.loads(process.stdout)['rows']
        if len(result)!=len(rows):raise ValueError('product coverage')
        updates=[]
        for source,generated in zip(rows,result):
            if any(source[k]!=generated[k] for k in ('sheet','sheet_id','row','stock')):raise ValueError('generated identity')
            if {c['column'] for c in generated['cells']}!=set(HEADERS):raise ValueError('column coverage')
            updates.extend((generated['sheet'],generated['row'],c['column']+1,c['value']) for c in generated['cells'])
        validate(updates,before)
        if not updates:raise ValueError('no eligible products')
        after=snapshot();preflight(before,after,updates)
        current_ids={ws.title:ws.id for ws in sp.worksheets()}
        if any(current_ids.get(tab)!=sheet_id for tab,sheet_id in TABS.items()):raise ValueError('sheet identity drift')
        response=sp.values_batch_update({'valueInputOption':'RAW','data':ranges_for(updates)})
        readback={tab:ws.get_all_values() for tab,ws in worksheets.items()}
        for tab,row,col,value in updates:
            if cell(readback[tab][row-1],col-1)!=value:raise RuntimeError('readback mismatch')
        validate(updates,{tab:(values,after[tab][1],after[tab][2]) for tab,values in readback.items()})
        print(json.dumps({'status':'verified','products':len({(t,r) for t,r,c,v in updates}),'verified_cells':len(updates),'duplicate_full_sets':0,'reported_updated_cells':response.get('totalUpdatedCells')}))
    except Exception as e:
        print('Service account principal:',credential.service_account_email)
        code=getattr(getattr(e,'response',None),'status_code',None)
        print(json.dumps({'status':'aborted','error_type':type(e).__name__,'http_status':code}))
        raise SystemExit(1)

if __name__=='__main__':main()
