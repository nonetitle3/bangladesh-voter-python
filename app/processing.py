import re, unicodedata
from datetime import datetime
import fitz, numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract
from pytesseract import Output

BENGALI_RE=re.compile(r'[\u0980-\u09FF]'); MOJIBAKE=('Ï','Ɓ','ė','ĥ','×','Î','Ð','Ý','�')

def normalize_bengali(text):
    if not text:return ''
    text=unicodedata.normalize('NFC',str(text)); text=re.sub(r'[\u200b\u200c\u200d\ufeff]','',text).replace('\u00a0',' ')
    for _ in range(3):
        fixed=re.sub(r'([\u09BF-\u09CC])([\u0985-\u09B9\u09DC-\u09DF])',r'\2\1',text)
        fixed=re.sub(r'([\u09BF-\u09CC])([\u09CD])([\u0985-\u09B9\u09DC-\u09DF])',r'\2\3\1',fixed)
        fixed=re.sub(r'([\u0985-\u09B9\u09DC-\u09DF])\s+([\u09BF-\u09CC])',r'\1\2',fixed)
        if fixed==text: break
        text=fixed
    return re.sub(r'[ \t]+',' ',text).strip()

def clean(value):
    value=normalize_bengali(value); value=re.sub(r'^[\s:：\-]+|[\s:：\-]+$','',value); return value or None

def quality(text):
    if not text:return -1
    return min(len(re.sub(r'\s+','',text)),600)+len(BENGALI_RE.findall(text))*4-sum(text.count(x) for x in MOJIBAKE)*80

def line_groups(tokens,gap=24):
    groups=[]
    for t in sorted(tokens,key=lambda x:(x['top'],x['left'])):
        cy=t['top']+t['height']/2; target=None
        for g in groups:
            if abs(cy-g['cy'])<=gap: target=g; break
        if target is None: groups.append({'cy':cy,'items':[t]})
        else:
            target['items'].append(t); target['cy']=sum(x['top']+x['height']/2 for x in target['items'])/len(target['items'])
    return [' '.join(x['text'] for x in sorted(g['items'],key=lambda z:z['left'])) for g in sorted(groups,key=lambda z:z['cy'])]

def ocr_lines(image,config='--psm 6'):
    d=pytesseract.image_to_data(image,lang='ben+eng',config=config,output_type=Output.DICT); tokens=[]
    for i,raw in enumerate(d.get('text',[])):
        text=normalize_bengali(raw)
        if not text:continue
        tokens.append({'text':text,'left':int(d['left'][i]),'top':int(d['top'][i]),'width':int(d['width'][i]),'height':int(d['height'][i])})
    return line_groups(tokens,26)

def value_after(text,labels):
    for label in labels:
        m=re.search(rf'(?:{label})\s*[:：\-]?\s*(.+)$',text,re.I)
        if m:return clean(m.group(1))
    return None

def parse_card(lines):
    lines=[clean(x) for x in lines if clean(x)]
    lines=[x for x in lines if not re.match(r'^(ফরম|ছবি|পুরুষ|চূড়ান্ত|চূড়া|বাংলাদেশ|নির্বাচন)',x or '')]
    if not lines:return {}
    r={'serial_no':None,'name':None,'voter_id':None,'father_name':None,'mother_name':None,'occupation':None,'birth_date':None,'address':None,'raw_text':'\n'.join(lines)}
    m=re.search(r'([০-৯0-9]{3})\s*\.?',lines[0]);
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
            raw=dm.group(1).translate(str.maketrans('০১২৩৪৫৬৭৮৯','0123456789'))
            for fmt in ('%d/%m/%Y','%d-%m-%Y'):
                try:r['birth_date']=datetime.strptime(raw,fmt).date(); break
                except ValueError:pass
        occ=re.sub(r'^(পেশা|occupation)\s*[:：-]?\s*','',fifth,flags=re.I); r['occupation']=clean(occ[:dm.start()].rstrip(' ,।') if dm else occ)
    if len(lines)>5:r['address']=value_after(lines[5],[r'ঠিকানা',r'address']) or clean(re.sub(r'^(ঠিকানা|address)\s*[:：-]?\s*','',lines[5],flags=re.I))
    return r

def grid_bounds(image):
    a=np.asarray(image.convert('L')); dark=a<170; h,w=dark.shape; y0=int(h*.17)
    yc=dark[y0:,:].sum(axis=1); ys=np.where(yc>w*.55)[0]+y0; groups=[]
    for y in ys:
        if not groups or y-groups[-1][-1]>8:groups.append([int(y)])
        else:groups[-1].append(int(y))
    yl=[int(sum(g)/len(g)) for g in groups if len(g)>=2]
    best=None
    for i in range(max(0,len(yl)-10),len(yl)-5):
        seq=yl[i:i+6]; gaps=[seq[j+1]-seq[j] for j in range(5)]
        if all(70<g<h*.22 for g in gaps):best=seq
    if not best:return None
    xc=dark[best[0]:best[-1],:].sum(axis=0); xs=np.where(xc>(best[-1]-best[0])*.60)[0]; groups=[]
    for x in xs:
        if not groups or x-groups[-1][-1]>8:groups.append([int(x)])
        else:groups[-1].append(int(x))
    xl=[int(sum(g)/len(g)) for g in groups if len(g)>=2]
    for i in range(len(xl)-3):
        seq=xl[i:i+4]; gaps=[seq[j+1]-seq[j] for j in range(3)]
        if min(gaps)>w*.20 and max(gaps)/min(gaps)<1.25:return seq,best
    return None

def extract_cards(page):
    pix=page.get_pixmap(matrix=fitz.Matrix(3.5,3.5),alpha=False); image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples); grid=grid_bounds(image)
    if not grid:return []
    xs,ys=grid; records=[]
    for row in range(5):
        for col in range(3):
            cell=image.crop((xs[col]+7,ys[row]+7,xs[col+1]-7,ys[row+1]-7)); candidates=[]
            for config in ('--psm 6','--psm 11'):
                ls=ocr_lines(cell,config); candidates.append((quality('\n'.join(ls)),ls))
            enhanced=ImageOps.grayscale(cell); enhanced=ImageEnhance.Contrast(enhanced).enhance(1.35); ls=ocr_lines(enhanced,'--psm 6'); candidates.append((quality('\n'.join(ls)),ls))
            _,best=max(candidates,key=lambda x:x[0]); rec=parse_card(best)
            if rec.get('name') or rec.get('voter_id'):rec['ocr_used']=True; records.append(rec)
    return records

def location_metadata(doc):
    # Page 1-2 are location pages; never create voter records from them.
    texts=[]
    for i in range(min(2,len(doc))):
        native=doc[i].get_text('text') or ''
        if native and not any(x in native for x in MOJIBAKE):texts.append(native); continue
        pix=doc[i].get_pixmap(matrix=fitz.Matrix(3,3),alpha=False); img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
        texts.append(max([pytesseract.image_to_string(img,lang='ben+eng',config=c) for c in ('--psm 6','--psm 11')],key=quality,default=''))
    text=normalize_bengali('\n'.join(texts)); out={}
    patterns={'district':r'জেলা\s*[:：]?\s*([^\n]+)','upazila':r'উপজেলা(?:/থানা)?\s*[:：]?\s*([^\n]+)','union_name':r'ইউনিয়ন[^\n]*?[:：]\s*([^\n]+)','ward':r'ওয়ার্ড\s*নম্বর[^\n]*?[:：]\s*([০-৯0-9]+)','post_code':r'পোস্টকোড\s*[:：]?\s*([০-৯0-9]+)','division':r'অঞ্চল\s*[:：]?\s*([^\n]+)','address':r'ভোটার এলাকার নাম\s*[:：]?\s*([^\n]+)'}
    for k,p in patterns.items():
        m=re.search(p,text,re.I)
        if m and clean(m.group(1)):out[k]=clean(m.group(1))
    return out

def apply_location(r,loc):
    for k in ('district','upazila','union_name','ward','post_code','division'):
        if not r.get(k) and loc.get(k):r[k]=loc[k]
    if not r.get('address') and loc.get('address'):r['address']=loc['address']
    return r

def process_pdf(file_path,progress_callback=None):
    results=[]; any_ocr=False
    def progress(page,total,stage,records=0):
        if progress_callback:
            try:progress_callback(page,total,stage,records)
            except Exception:pass
    with fitz.open(file_path) as doc:
        total=len(doc); progress(0,total,'reading PDF',0); loc=location_metadata(doc); progress(min(2,total),total,'reading location pages',0)
        for pn in range(3,total+1):
            progress(pn,total,'OCR page',len(results)); page_records=extract_cards(doc[pn-1])
            for r in page_records:
                if not r.get('name') and not r.get('voter_id'):continue
                r=apply_location(r,loc); r.update(page_number=pn,ocr_used=True,confidence=.90); results.append(r)
            any_ocr=True; progress(pn,total,'saving records',len(results))
    progress(total,total,'completed',len(results)); return results,any_ocr,total
