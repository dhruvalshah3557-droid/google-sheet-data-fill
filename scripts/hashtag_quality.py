"""Unicode-aware hashtag checks; report suspect content without replacing copy."""
import re
import unicodedata
from collections import Counter, defaultdict

def tokens(value):
    # Letters, numbers and combining marks belong to one native-script tag.
    result=[]
    for part in str(value or '').split('#')[1:]:
        chars=[]
        for ch in part:
            if ch=='_' or unicodedata.category(ch)[0] in 'LMN': chars.append(ch)
            else: break
        if chars: result.append(unicodedata.normalize('NFC',''.join(chars)).casefold())
    return result

def is_reference(tag):
    return bool(re.fullmatch(r'(?:stock|stk|sku|ref|reference|product|cd)?[_-]*\d+(?:_\d+)*',tag))

def signature(value, ignore_reference=False):
    return tuple(sorted(set(t for t in tokens(value) if not (ignore_reference and is_reference(t)))))

SCRIPTS={'thai':(0xe00,0xe7f),'kannada':(0xc80,0xcff),'telugu':(0xc00,0xc7f),'telagu':(0xc00,0xc7f),'malayalam':(0xd00,0xd7f),'chinese':(0x4e00,0x9fff),'japanese':(0x3040,0x30ff),'korean':(0xac00,0xd7af),'arabic':(0x600,0x6ff),'hindi':(0x900,0x97f),'tamil':(0xb80,0xbff),'russian':(0x400,0x4ff),'greek':(0x370,0x3ff),'burmese':(0x1000,0x109f),'myanmar':(0x1000,0x109f)}
GENERIC={'colourdiam','diamond','diamonds','jewellery','jewelry','luxury','luxuryjewellery','naturaldiamonds','fancycolordiamonds','fancycolourdiamonds','finejewellery','เพชร','เครื่องประดับ','หรูหรา'}

def cell_issues(name,value):
    name=name.lower();tags=tokens(value);issues=[]
    if 'vestslavic' in name: return ['ambiguous hashtag language']
    if not tags: return ['no valid hashtags']
    if len(tags)!=len(set(tags)): issues.append('duplicate hashtag inside cell')
    for lang,(lo,hi) in SCRIPTS.items():
        if lang in name and not any(lo<=ord(ch)<=hi for tag in tags for ch in tag): issues.append('hashtag language script mismatch')
    meaningful=[t for t in tags if not is_reference(t) and t not in GENERIC]
    if not meaningful: issues.append('generic-only hashtag set')
    return issues

def audit_hashtags(title,values):
    if not values or 'STK' not in values[0]: return []
    ki=values[0].index('STK');findings=[];sets=defaultdict(list);templates=defaultdict(list)
    for ri,row in enumerate(values[1:],2):
        stock=str(row[ki] if ki<len(row) else '').strip()
        if not stock:continue
        for ci,name in enumerate(values[0]):
            if not any(s in str(name).lower() for s in ('hashtag','hahstag')): continue
            value=row[ci] if ci<len(row) else ''
            if not str(value).strip():continue
            item={'tab':title,'row':ri,'stock':stock,'header':name,'column':ci+1,'severity':'warning'}
            findings.extend({**item,'issue':issue} for issue in cell_issues(str(name),value))
            if 'vestslavic' in str(name).lower():continue
            if signature(value):sets[(ci,signature(value))].append(item)
            if signature(value,True):templates[(ci,signature(value,True))].append(item)
    for issue,groups in [('repeated full hashtag set',sets),('hashtag template differs only by stock reference',templates)]:
        for items in groups.values():
            stocks=sorted(set(x['stock'] for x in items))
            if len(stocks)>1:findings.append({'tab':title,'header':items[0]['header'],'column':items[0]['column'],'severity':'warning','issue':issue,'stocks':stocks})
    return findings
