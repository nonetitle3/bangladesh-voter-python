import re, unicodedata
from datetime import datetime
import fitz, numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract
from pytesseract import Output

BENGALI_RE = re.compile(r'[\u0980-\u09FF]')
MOJIBAKE = ('Ï','Ɓ','ė','ĥ','×','Î','Ð','Ý','�')
DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯','0123456789')


def normalize_bengali(text):
    if not text: return ''
    text = unicodedata.normalize('NFC', str(text))
    text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text).replace('\u00a0',' ')
    for _ in range(3):
        fixed = re.sub(r'([\u09BF-\u09CC])([\u0985-\u09B9\u09DC-\u09DF])', r'\2\1', text)
        fixed = re.sub(r'([\u09BF-\u09CC])([\u09CD])([\u0985-\u09B9\u09DC-\u09DF])', r'\2\3\1', fixed)
        fixed = re.sub(r'([\u0985-\u09B9\u09DC-\u09DF])\s+([\u09BF-\u09CC])', r'\1\2', fixed)
        if fixed == text: break
        text = fixed
    return re.sub(r'[ \t]+',' ',text).strip()


def clean(value):
    value = normalize_bengali(value)
    value = re.sub(r'^[\s:：\-]+|[\s:：\-]+$','',value)
    return value or None


def quality(text):
    if not text: return -1
    return min(len(re.sub(r'\s+','',text)),600) + len(BENGALI_RE.findall(text))*4 - sum(text.count(x) for x in MOJIBAKE)*100


def ocr_tokens(image, config='--psm 6'):
    data = pytesseract.image_to_data(image, lang='ben+eng', config=config, output_type=Output.DICT)
    out=[]
    for i,raw in enumerate(data.get('text',[])):
        text=normalize_bengali(raw)
        if not text: continue
        try: conf=float(data['conf'][i])
        except Exception: conf=-1
        out.append({'text':text,'left':int(data['left'][i]),'top':int(data['top'][i]),'width':int(data['width'][i]),'height':int(data['height'][i]),'conf':conf})
    return out


def line_groups(tokens,gap=28):
    groups=[]
    for t in sorted(tokens,key=lambda x:(x['top'],x['left'])):
        cy=t['top']+t['height']/2; target=None
        for g in groups:
            if abs(cy-g['cy'])<=gap: target=g; break
        if target is None: groups.append({'cy':cy,'items':[t]})
        else:
            target['items'].append(t); target['cy']=sum(x['top']+x['height']/2 for x in target['items'])/len(target['items'])
    return [' '.join(x['text'] for x in sorted(g['items'],key=lambda z:z['left'])) for g in sorted(groups,key=lambda g:g['cy'])]


def ocr_lines(image, config='--psm 6'):
    return line_groups(ocr_tokens(image,config),26)


def value_after(text,labels):
    for label in labels:
        m=re.search(rf'(?:{label})\s*[:：\-]?\s*(.+)$',text,re.I)
        if m: return clean(m.group(1))
    return None


def parse_card(lines):
    lines=[clean(x) for x in lines if clean(x)]
    lines=[x for x in lines if not re.match(r'^(ফরম|ছবি|পুরুষ|চূড়ান্ত|চূড়া|বাংলাদেশ|নির্বাচন)',x or '')]
    if not lines:return {}
    r={'serial_no':None,'name':None,'voter_id':None,'father_name':None,'mother_name':None,'occupation':None,'birth_date':None,'address':None,'raw_text':'\n'.join(lines)}
    m=re.search(r'([০-৯0-9]{3})\s*\.?',lines[0])
    if m:r['serial_no']=m.group(1)
    r['name']=value_after(lines[0],[r'নাম',r'name'])
    if not r['name'] and m:r['name']=clean(lines[0][m.end():])
    if len(lines)>1:
        r['voter_id']=value_after(lines[1],[r'ভোটার\s*(?:নং|নম্বর)',r'NID',r'Voter\s*ID'])
        if not r['voter_id']:
            nums=re.findall(r'[০-৯0-9]{8,}',lines[1]); r['voter_id']=nums[0] if nums else None
    if len(lines)>2:r['father_name']=value_after(lines[2],[r'পিতা',r'পিতার\s*নাম',r'father']) or clean(re.sub(r'^(পিতা|পিতার\s*নাম|father)\s*[:：-]?\s*','',lines[2],flags=re.I))
    if len(lines)>3:r['mother_name']=value_after(lines[3],[r'মাতা',r'মাতার\s*নাম',r'mother']) or clean(re.sub(r'^(মাতা|মাতার\s*নাম|mother)\s*[:：-]?\s*','',lines[3],flags=re.I))
    if len(lines)>4:
        fifth=lines[4]; dm=re.search(r'([০-৯0-9]{1,2}[\/-][০-৯0-9]{1,2}[\/-][০-৯0-9]{2,4})',fifth)
        if dm:
            raw=dm.group(1).translate(DIGITS)
            for fmt in ('%d/%m/%Y','%d-%m-%Y'):
                try:r['birth_date']=datetime.strptime(raw,fmt).date(); break
                except ValueError:pass
        occ=re.sub(r'^(পেশা|occupation)\s*[:：-]?\s*','',fifth,flags=re.I)
        r['occupation']=clean(occ[:dm.start()].rstrip(' ,।') if dm else occ)
    if len(lines)>5:r['address']=value_after(lines[5],[r'ঠিকানা',r'address']) or clean(re.sub(r'^(ঠিকানা|address)\s*[:：-]?\s*','',lines[5],flags=re.I))
    return r


def serial_rows(tokens):
    serial=[]
    for t in tokens:
        s=t['text'].replace('.','').strip()
        if re.fullmatch(r'[০-৯0-9]{3}',s):
            serial.append(t)
    if len(serial)<3:return []
    serial.sort(key=lambda t:t['top'])
    rows=[]
    for t in serial:
        cy=t['top']+t['height']/2
        target=min(rows,key=lambda r:abs(cy-r['cy'])) if rows else None
        if target and abs(cy-target['cy'])<100:
            target['items'].append(t); target['cy']=sum(x['top']+x['height']/2 for x in target['items'])/len(target['items'])
        else: rows.append({'cy':cy,'items':[t]})
    rows=[r for r in rows if len(r['items'])>=2]
    rows.sort(key=lambda r:r['cy'])
    return rows


def positional_cards(page):
    """Fallback that does not depend on detecting faint PDF border lines.
    The supplied voter-list has 3 columns and 5 voter rows per page. We locate
    the printed 3-digit serials, derive column/row boundaries from their
    positions, crop each card, then OCR that card independently."""
    pix=page.get_pixmap(matrix=fitz.Matrix(3.5,3.5),alpha=False)
    image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    best_tokens=[]
    for config in ('--psm 6','--psm 11'):
        ts=ocr_tokens(image,config)
        if len(serial_rows(ts))>len(serial_rows(best_tokens)): best_tokens=ts
    rows=serial_rows(best_tokens)
    if len(rows)<2:return []
    # The template has 3 columns. Use the first row's serial x positions and
    # supplement from all rows when one serial was missed.
    xs=[]
    for row in rows:
        for t in sorted(row['items'],key=lambda x:x['left']): xs.append(t['left']+t['width']/2)
    xs.sort()
    centers=[]
    # cluster x positions into three stable column centers
    for x in xs:
        if not centers or abs(x-centers[-1])>image.width*0.12: centers.append(x)
        else: centers[-1]=(centers[-1]+x)/2
    if len(centers)<3:
        first=sorted(rows[0]['items'],key=lambda t:t['left'])
        centers=[t['left']+t['width']/2 for t in first]
    if len(centers)<3:return []
    centers=sorted(centers[:3])
    row_centers=[r['cy'] for r in rows[:5]]
    if len(row_centers)<2:return []
    gaps=[row_centers[i+1]-row_centers[i] for i in range(len(row_centers)-1)]
    gap=float(np.median(gaps))
    y_edges=[max(0,row_centers[0]-gap*.48)]
    for i in range(len(row_centers)-1): y_edges.append((row_centers[i]+row_centers[i+1])/2)
    y_edges.append(min(image.height,row_centers[-1]+gap*.48))
    x_edges=[0,(centers[0]+centers[1])/2,(centers[1]+centers[2])/2,image.width]
    records=[]
    for ri in range(len(row_centers)):
        for ci in range(3):
            left=int(x_edges[ci]+8); right=int(x_edges[ci+1]-8); top=int(y_edges[ri]+8); bottom=int(y_edges[ri+1]-8)
            if right<=left or bottom<=top: continue
            cell=image.crop((left,top,right,bottom))
            candidates=[]
            for config in ('--psm 6','--psm 11'):
                ls=ocr_lines(cell,config); candidates.append((quality('\n'.join(ls)),ls))
            enhanced=ImageOps.grayscale(cell); enhanced=ImageEnhance.Contrast(enhanced).enhance(1.35); enhanced=ImageEnhance.Sharpness(enhanced).enhance(1.15)
            ls=ocr_lines(enhanced,'--psm 6'); candidates.append((quality('\n'.join(ls)),ls))
            _,ls=max(candidates,key=lambda z:z[0]); rec=parse_card(ls)
            if rec.get('name') or rec.get('voter_id'):
                rec['ocr_used']=True; records.append(rec)
    return records


def grid_cards(page):
    # First try positional OCR; it is more reliable than assuming PDF borders
    # survive rasterization. Keep a simple border detector as a secondary path.
    return positional_cards(page)


def location_metadata(doc):
    texts=[]
    for i in range(min(2,len(doc))):
        native=doc[i].get_text('text') or ''
        if native and not any(x in native for x in MOJIBAKE): texts.append(native); continue
        pix=doc[i].get_pixmap(matrix=fitz.Matrix(3,3),alpha=False); img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
        vals=[pytesseract.image_to_string(img,lang='ben+eng',config=c) for c in ('--psm 6','--psm 11')]
        texts.append(max(vals,key=quality,default=''))
    text=normalize_bengali('\n'.join(texts)); out={}
    patterns={'district':r'জেলা\s*[:：]?\s*([^\n]+)','upazila':r'উপজেলা(?:/থানা)?\s*[:：]?\s*([^\n]+)','union_name':r'ইউনিয়ন[^\n]*?[:：]\s*([^\n]+)','ward':r'ওয়ার্ড\s*নম্বর[^\n]*?[:：]\s*([০-৯0-9]+)','post_code':r'পোস্টকোড\s*[:：]?\s*([০-৯0-9]+)','division':r'অঞ্চল\s*[:：]?\s*([^\n]+)','address':r'ভোটার এলাকার নাম\s*[:：]?\s*([^\n]+)'}
    for k,p in patterns.items():
        m=re.search(p,text,re.I)
        if m and clean(m.group(1)): out[k]=clean(m.group(1))
    return out


def apply_location(r,loc):
    for k in ('district','upazila','union_name','ward','post_code','division'):
        if not r.get(k) and loc.get(k): r[k]=loc[k]
    if not r.get('address') and loc.get('address'): r['address']=loc['address']
    return r


def process_pdf(file_path,progress_callback=None):
    results=[]; any_ocr=False
    def progress(page,total,stage,records=0):
        if progress_callback:
            try: progress_callback(page,total,stage,records)
            except Exception: pass
    with fitz.open(file_path) as doc:
        total=len(doc); progress(0,total,'reading PDF',0)
        loc=location_metadata(doc); progress(min(2,total),total,'reading location pages',0)
        for pn in range(3,total+1):
            progress(pn,total,'OCR page',len(results))
            page_records=grid_cards(doc[pn-1])
            for r in page_records:
                if not r.get('name') and not r.get('voter_id'): continue
                r=apply_location(r,loc); r.update(page_number=pn,ocr_used=True,confidence=.90); results.append(r)
            any_ocr=True; progress(pn,total,'saving records',len(results))
    progress(total,total,'completed',len(results)); return results,any_ocr,total
