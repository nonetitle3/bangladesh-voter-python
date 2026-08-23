import re, unicodedata
from datetime import datetime
import fitz, numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract
from pytesseract import Output

LABELS={'voter_id':[r'ভোটার\s*(?:নং|নম্বর)',r'NID',r'Voter\s*ID'],'serial_no':[r'ক্রমিক',r'সিরিয়াল',r'সিরিয়াল',r'serial'],'name':[r'নাম',r'name'],'father_name':[r'পিতা',r'পিতার\s*নাম',r'father'],'mother_name':[r'মাতা',r'মাতার\s*নাম',r'mother'],'birth_date':[r'জন্ম\s*তারিখ',r'DOB',r'date\s*of\s*birth'],'occupation':[r'পেশা',r'occupation'],'gender':[r'লিঙ্গ',r'gender'],'address':[r'ঠিকানা',r'ঠিকানা\s*ঃ?',r'address'],'village':[r'গ্রাম',r'village'],'ward':[r'ওয়ার্ড',r'ওয়ার্ড',r'ওয়র্ড',r'ward'],'union_name':[r'ইউনিয়ন',r'ইউনিয়ন',r'union'],'upazila':[r'উপজেলা',r'upazila'],'district':[r'জেলা',r'district'],'division':[r'বিভাগ',r'division'],'post_code':[r'পোস্ট\s*কোড',r'পোস্টকোড',r'post\s*code']}
ALL=[p for ps in LABELS.values() for p in ps]; NEXT=re.compile(r'(?:'+'|'.join(ALL)+r')\s*[:：-]?',re.I)
BENGALI=re.compile(r'[\u0980-\u09FF]'); CONSONANT=r'[\u0985-\u09B9\u09DC-\u09DF]'; VOWEL=r'[\u09BF-\u09CC]'; MOJIBAKE=('Ï','Ɓ','ė','ĥ','×','Î','Ð','Ý','�')
SERIAL_RE=re.compile(r'^(?:[০-৯]{3}|\d{3})\.?$')


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
    compact=re.sub(r'\s+','',text); b=len(BENGALI.findall(text)); bad=text.count('�'); labels=sum(bool(re.search(p,text)) for p in (r'নাম',r'পিতা',r'মাতা',r'ঠিকানা',r'জেলা',r'উপেজলা',r'উপজেলা',r'ইউনিয়ন',r'ওয়ার্ড'))
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


def _ocr_tokens(page, scale=4.0, config='--psm 6'):
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
    image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    data=pytesseract.image_to_data(image,lang='ben+eng',config=config,output_type=Output.DICT)
    tokens=[]
    for i,raw in enumerate(data.get('text',[])):
        text=normalize_bengali((raw or '').strip())
        if not text:continue
        try: conf=float(data['conf'][i])
        except (ValueError,TypeError): conf=-1
        tokens.append({'text':text,'left':int(data['left'][i]),'top':int(data['top'][i]),'width':int(data['width'][i]),'height':int(data['height'][i]),'conf':conf})
    return image,tokens


def _serial_tokens(tokens):
    return [t for t in tokens if SERIAL_RE.match(t['text'])]


def _reconstruct_cells(tokens):
    """Rebuild each voter card from OCR word positions.

    The PDF's visible page has 3 columns x 5 voter cards. Whole-page OCR is
    actually readable, but its text stream interleaves the columns. Keeping
    the OCR coordinates lets us put every word back into its original card.
    """
    serials=_serial_tokens(tokens)
    if len(serials)<6:return []
    serials=sorted(serials,key=lambda t:(t['top'],t['left']))
    # Cluster serials into row bands using their vertical centers.
    rows=[]
    for s in serials:
        cy=s['top']+s['height']/2
        placed=False
        for row in rows:
            if abs(cy-row['cy'])<90:
                row['items'].append(s); row['cy']=(row['cy']*(len(row['items'])-1)+cy)/len(row['items']); placed=True; break
        if not placed:rows.append({'cy':cy,'items':[s]})
    rows=sorted(rows,key=lambda r:r['cy'])
    rows=[r for r in rows if len(r['items'])>=2]
    if len(rows)<2:return []
    # Column centers come from serial x positions. Three columns are stable.
    all_serials=[s for r in rows for s in r['items']]
    xs=sorted([s['left']+s['width']/2 for s in all_serials])
    # Derive three centers from the first row when possible; otherwise quantize by x.
    first=sorted(rows[0]['items'],key=lambda t:t['left'])
    if len(first)>=3:
        centers=[s['left']+s['width']/2 for s in first[:3]]
    else:
        centers=[]
        for x in xs:
            if not centers or abs(x-centers[-1])>500:centers.append(x)
    if len(centers)<3:return []
    centers=sorted(centers[:3])
    # Row boundaries are halfway between serial rows. Ignore page header and footer.
    row_bounds=[]
    for idx,row in enumerate(rows):
        top=row['cy']-65 if idx==0 else (rows[idx-1]['cy']+row['cy'])/2
        bottom=(row['cy']+rows[idx+1]['cy'])/2 if idx+1<len(rows) else row['cy']+260
        row_bounds.append((top,bottom,row))
    records=[]
    for top,bottom,row in row_bounds:
        for serial in sorted(row['items'],key=lambda t:t['left']):
            cx=serial['left']+serial['width']/2
            col=min(range(3),key=lambda i:abs(cx-centers[i]))
            left_edge=(centers[col-1]+centers[col])/2 if col>0 else 0
            right_edge=(centers[col]+centers[col+1])/2 if col<2 else 10**9
            cell_tokens=[t for t in tokens if top-10<=t['top']+t['height']/2<bottom+10 and left_edge<=t['left']+t['width']/2<right_edge]
            # Rebuild OCR lines by y proximity, preserving x order.
            line_groups=[]
            for t in sorted(cell_tokens,key=lambda z:(z['top'],z['left'])):
                cy=t['top']+t['height']/2
                target=None
                for g in line_groups:
                    if abs(cy-g['cy'])<32:
                        target=g; break
                if target is None:line_groups.append({'cy':cy,'items':[t]})
                else:target['items'].append(t); target['cy']=(target['cy']*(len(target['items'])-1)+cy)/len(target['items'])
            lines=[' '.join(x['text'] for x in sorted(g['items'],key=lambda z:z['left'])) for g in sorted(line_groups,key=lambda z:z['cy'])]
            # Drop the page header if it leaked into a cell and force the serial.
            lines=[x for x in lines if not re.match(r'^(ফরম|ছবি|পুরুষ|চূড়ান্ত|চূড়াĢ|অঞ্চল|জেলা|উপজেলা)',x)]
            text='\n'.join(lines)
            record=parse_record(text)
            serial_value=serial['text'].replace('.','')
            record['serial_no']=serial_value
            if record.get('name') or record.get('voter_id'):
                record['raw_text']=text
                record['ocr_used']=True
                records.append(record)
    return records


def extract_grid_records(page):
    image,tokens=_ocr_tokens(page,4.0,'--psm 6')
    records=_reconstruct_cells(tokens)
    if len(records)>=3:return records
    # Fallback to PSM 11 coordinate reconstruction if PSM 6 misses serials.
    _,tokens11=_ocr_tokens(page,4.0,'--psm 11')
    records=_reconstruct_cells(tokens11)
    return records if records else None


def extract_page(page):
    # Pages 3+ of this voter-list template are 3-column voter cards.
    grid=extract_grid_records(page)
    if grid:return grid,True
    native=page.get_text('text') or ''
    if native_text_is_good(native):return [parse_record(normalize_bengali(native))],False
    _,tokens=_ocr_tokens(page,3.0,'--psm 6')
    text=' '.join(t['text'] for t in sorted(tokens,key=lambda x:(x['top'],x['left'])))
    return [parse_record(text)],bool(text.strip())


def extract_location_metadata(page):
    native=page.get_text('text') or ''; candidates=[]
    if native_text_is_good(native):candidates.append(native)
    pix=page.get_pixmap(matrix=fitz.Matrix(3,3),alpha=False); image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
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
    def progress(page,total,stage,records=0):
        if progress_callback:
            try:progress_callback(page,total,stage,records)
            except Exception:pass
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
