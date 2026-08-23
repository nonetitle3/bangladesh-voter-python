import re, unicodedata
from datetime import datetime
import fitz, numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract

LABELS={'voter_id':[r'ভোটার\s*(?:নং|নম্বর)',r'NID',r'Voter\s*ID'],'serial_no':[r'ক্রমিক',r'সিরিয়াল',r'সিরিয়াল',r'serial'],'name':[r'নাম',r'name'],'father_name':[r'পিতা',r'পিতার\s*নাম',r'father'],'mother_name':[r'মাতা',r'মাতার\s*নাম',r'mother'],'birth_date':[r'জন্ম\s*তারিখ',r'DOB',r'date\s*of\s*birth'],'occupation':[r'পেশা',r'occupation'],'gender':[r'লিঙ্গ',r'gender'],'address':[r'ঠিকানা',r'ঠিকানা\s*ঃ?',r'address'],'village':[r'গ্রাম',r'village'],'ward':[r'ওয়ার্ড',r'ওয়ার্ড',r'ওয়র্ড',r'ward'],'union_name':[r'ইউনিয়ন',r'ইউনিয়ন',r'union'],'upazila':[r'উপজেলা',r'upazila'],'district':[r'জেলা',r'district'],'division':[r'বিভাগ',r'division'],'post_code':[r'পোস্ট\s*কোড',r'পোস্টকোড',r'post\s*code']}
ALL=[p for ps in LABELS.values() for p in ps]; NEXT=re.compile(r'(?:'+'|'.join(ALL)+r')\s*[:：-]?',re.I)
BENGALI=re.compile(r'[\u0980-\u09FF]'); CONSONANT=r'[\u0985-\u09B9\u09DC-\u09DF]'; VOWEL=r'[\u09BF-\u09CC]'; MOJIBAKE=('Ï','Ɓ','ė','ĥ','×','Î','Ð','Ý','�')

def normalize_bengali(text):
    if not text:return text
    text=unicodedata.normalize('NFC',str(text)); text=re.sub(r'[\u200b\u200c\u200d\ufeff]','',text).replace('\u00a0',' ')
    for _ in range(3):
        fixed=re.sub(rf'({VOWEL})({CONSONANT})',r'\2\1',text); fixed=re.sub(rf'({VOWEL})([\u09CD])({CONSONANT})',r'\2\3\1',fixed)
        if fixed==text:break
        text=fixed
    text=re.sub(rf'({CONSONANT})\s+({VOWEL})',r'\1\2',text); text=re.sub(rf'({VOWEL})\s+({CONSONANT})',r'\1\2',text)
    return re.sub(r'[ \t]+',' ',text).strip()

def preprocess(image,threshold=175):
    image=ImageOps.grayscale(image); image=ImageEnhance.Contrast(image).enhance(1.6); image=ImageEnhance.Sharpness(image).enhance(1.25); image=image.filter(ImageFilter.SHARPEN); return image.point(lambda p:255 if p>threshold else 0)

def native_text_is_good(text):
    text=text or ''
    if any(x in text for x in MOJIBAKE):return False
    compact=re.sub(r'\s+','',text)
    return len(compact)>=80 and len(BENGALI.findall(text))>=15 and sum(bool(re.search(p,text)) for p in (r'নাম',r'পিতা',r'মাতা',r'ঠিকানা',r'জেলা',r'উপজেলা'))>=2

def ocr_quality(text):
    if not text:return -1
    compact=re.sub(r'\s+','',text); b=len(BENGALI.findall(text)); bad=text.count('�'); labels=sum(bool(re.search(p,text)) for p in (r'নাম',r'পিতা',r'মাতা',r'ঠিকানা',r'জেলা',r'উপজেলা',r'ইউনিয়ন',r'ওয়ার্ড'))
    return min(len(compact),500)+b*3+labels*100-bad*100

def clean_field(value):
    if not value:return None
    value=normalize_bengali(value); value=re.sub(r'\s*[:：]\s*',': ',value); value=re.sub(r'\s{2,}',' ',value); return value.strip(' -:：') or None

def _cut(value):
    m=NEXT.search(value or ''); return value[:m.start()] if m and m.start()>0 else value

def value_after_label(lines,patterns,multiline=False):
    for i,line in enumerate(lines):
        for p in patterns:
            m=re.search(rf'(?:{p})\s*[:：\-]?\s*(.*)$',line,re.I)
            if m:
                first=clean_field(_cut(m.group(1)))
                if first:
                    if not multiline:return first
                    vals=[first]; j=i+1
                    while j<len(lines) and lines[j].strip() and not NEXT.search(lines[j]):vals.append(lines[j].strip()); j+=1
                    return clean_field(' '.join(vals))
            if re.search(p,line,re.I) and i+1<len(lines):
                first=clean_field(_cut(lines[i+1]))
                if first:
                    if not multiline:return first
                    vals=[first]; j=i+2
                    while j<len(lines) and lines[j].strip() and not NEXT.search(lines[j]):vals.append(lines[j].strip()); j+=1
                    return clean_field(' '.join(vals))
    return None

def normalize_gender(value):
    if not value:return None
    v=value.strip().lower()
    if 'পুরুষ' in value or v in ('male','m'):return 'পুরুষ'
    if 'মহিলা' in value or 'নারী' in value or v in ('female','f'):return 'মহিলা'
    return clean_field(value)

def parse_record(text):
    text=normalize_bengali(text); lines=[normalize_bengali(re.sub(r'\s+',' ',x).strip()) for x in text.splitlines() if x.strip()]
    data={k:value_after_label(lines,p,multiline=(k=='address')) for k,p in LABELS.items()}; data['gender']=normalize_gender(data.get('gender'))
    if data.get('birth_date'):
        for fmt in ('%d/%m/%Y','%d-%m-%Y','%d.%m.%Y'):
            try:data['birth_date']=datetime.strptime(data['birth_date'].strip(),fmt).date(); break
            except ValueError:pass
    data['raw_text']=text; return data

def _group(values,gap=20):
    groups=[]
    for v in map(int,values):
        if not groups or v-groups[-1][-1]>gap:groups.append([v])
        else:groups[-1].append(v)
    return [int(sum(g)/len(g)) for g in groups if len(g)>=2]

def _grid_bounds(image):
    arr=np.asarray(image.convert('L')); h,w=arr.shape; binary=arr<180; yc=binary[:,int(w*.07):int(w*.96)].sum(axis=1); yl=_group(np.where(yc>w*.75)[0])
    if len(yl)<6:return None
    yl=yl[-6:]; y0,y1=int(h*.15),int(h*.90); xc=binary[y0:y1,:].sum(axis=0); xl=_group(np.where(xc>(y1-y0)*.65)[0])
    if len(xl)<4:return None
    merged=[]
    for x in xl:
        if not merged or x-merged[-1]>35:merged.append(x)
        else:merged[-1]=(merged[-1]+x)//2
    return (merged[:4],yl) if len(merged)>=4 else None

def _score(key,value):
    if not value:return -1
    s=len(BENGALI.findall(str(value)))*4+min(len(str(value)),80)-len(re.findall(r'[A-Za-z]',str(value)))*3
    if key=='voter_id':s+=len(re.findall(r'\d',str(value)))*8
    if key=='birth_date' and re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}',str(value)):s+=100
    return s

def _merge(records):
    out={}
    for key in set().union(*(r.keys() for r in records)) if records else []:
        vals=[r.get(key) for r in records if r.get(key)]
        if vals:out[key]=max(vals,key=lambda x:_score(key,x))
    return out

def _ocr_cell(image):
    t1=pytesseract.image_to_string(image,lang='ben+eng',config='--psm 4') or ''; texts=[t1]; parsed=[parse_record(t1)] if t1.strip() else []
    if not parsed or any(not parsed[0].get(k) for k in ('name','voter_id','father_name','mother_name','address')):
        t2=pytesseract.image_to_string(image,lang='ben+eng',config='--psm 11') or ''
        if t2.strip():texts.append(t2); parsed.append(parse_record(t2))
    out=_merge(parsed)
    if texts:out['raw_text']=max(texts,key=ocr_quality)
    return out

def extract_grid_records(page):
    pix=page.get_pixmap(matrix=fitz.Matrix(2.5,2.5),alpha=False); image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples); bounds=_grid_bounds(image)
    if not bounds:return None
    xs,ys=bounds; results=[]
    for row in range(5):
        for col in range(3):
            box=(xs[col]+8,ys[row]+8,xs[col+1]-8,ys[row+1]-8)
            if box[2]<=box[0] or box[3]<=box[1]:continue
            r=_ocr_cell(image.crop(box))
            if r.get('name') or r.get('voter_id'):r['ocr_used']=True; results.append(r)
    return results

def extract_page(page):
    native=page.get_text('text') or ''; markers=len(re.findall(r'\b\d{3}\.\s*নাম',normalize_bengali(native)))
    if markers>=3:
        grid=extract_grid_records(page)
        if grid:return grid,True
    if native_text_is_good(native):return [parse_record(normalize_bengali(native))],False
    pix=page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False); image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples); texts=[pytesseract.image_to_string(image,lang='ben+eng',config=c) or '' for c in ('--psm 6','--psm 11')]; best=max(texts,key=ocr_quality,default='')
    if ocr_quality(best)<260:
        enhanced=preprocess(image); texts.append(pytesseract.image_to_string(enhanced,lang='ben+eng',config='--psm 6') or ''); best=max(texts,key=ocr_quality,default=best)
    return [parse_record(normalize_bengali(best))],bool(best.strip())

def extract_location_metadata(page):
    native=page.get_text('text') or ''; candidates=[]
    if native_text_is_good(native):candidates.append(native)
    pix=page.get_pixmap(matrix=fitz.Matrix(2.5,2.5),alpha=False); image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    for c in ('--psm 6','--psm 11'):candidates.append(pytesseract.image_to_string(image,lang='ben+eng',config=c) or '')
    best=max(candidates,key=ocr_quality,default=''); data=parse_record(best); keys=('address','village','ward','union_name','upazila','district','division','post_code')
    return {k:data.get(k) for k in keys if data.get(k)}

def _merge_location(items):
    out={}
    for item in items:
        for k,v in item.items():
            if v and (k not in out or len(str(v))>len(str(out[k]))):out[k]=v
    return out

def _apply_location(record,metadata):
    for k,v in metadata.items():
        if not record.get(k):record[k]=v
    if not record.get('address'):
        parts=[record.get(k) or metadata.get(k) for k in ('village','ward','union_name','upazila','district','division','post_code')]; parts=[str(x).strip() for x in parts if x and str(x).strip()]
        if parts:record['address']=', '.join(dict.fromkeys(parts))
    return record

def process_pdf(file_path, progress_callback=None):
    results=[]; any_ocr=False
    def progress(page, total, stage, records=0):
        if progress_callback:
            try: progress_callback(page,total,stage,records)
            except Exception: pass
    with fitz.open(file_path) as doc:
        total=len(doc); progress(0,total,'reading PDF',0)
        location=_merge_location([extract_location_metadata(doc[i]) for i in range(min(2,total))])
        progress(min(2,total),total,'reading location pages',0)
        for page_number in range(3,total+1):
            progress(page_number,total,'OCR page',len(results))
            page_results,ocr=extract_page(doc[page_number-1]); any_ocr|=ocr
            for record in page_results:
                if len(re.sub(r'\s+','',record.get('raw_text') or ''))<15:continue
                record=_apply_location(record,location); record.update(page_number=page_number,ocr_used=ocr,confidence=.86 if ocr else .95); results.append(record)
            progress(page_number,total,'saving records',len(results))
    progress(total,total,'completed',len(results))
    return results,any_ocr,total
