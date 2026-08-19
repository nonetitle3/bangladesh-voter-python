import re
from datetime import datetime
from pathlib import Path
import fitz
from PIL import Image, ImageEnhance, ImageOps
import pytesseract

LABELS = {
    "voter_id": [r"ভোটার\s*(?:নং|নম্বর)", r"NID"],
    "serial_no": [r"ক্রমিক", r"সিরিয়াল", r"serial"],
    "name": [r"নাম", r"name"],
    "father_name": [r"পিতা", r"father"],
    "mother_name": [r"মাতা", r"mother"],
    "birth_date": [r"জন্ম\s*তারিখ", r"DOB", r"date\s*of\s*birth"],
    "occupation": [r"পেশা", r"occupation"],
    "gender": [r"লিঙ্গ", r"gender"],
    "address": [r"ঠিকানা", r"address"],
    "village": [r"গ্রাম", r"village"],
    "ward": [r"ওয়ার্ড", r"ওয়র্ড", r"ward"],
    "union_name": [r"ইউনিয়ন", r"ইউনিয়ন", r"union"],
    "upazila": [r"উপজেলা", r"upazila"],
    "district": [r"জেলা", r"district"],
    "division": [r"বিভাগ", r"division"],
    "post_code": [r"পোস্ট\s*কোড", r"post\s*code"],
}

def preprocess(image):
    image = ImageOps.grayscale(image)
    image = ImageEnhance.Contrast(image).enhance(1.8)
    return image.point(lambda p: 255 if p > 175 else 0)

def extract_page(page):
    text = page.get_text("text") or ""
    if len(re.sub(r"\s+", "", text)) >= 30:
        return text, False
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    image = preprocess(image)
    return pytesseract.image_to_string(image, lang="ben+eng", config="--psm 6"), True

def value_after_label(lines, patterns):
    for i, line in enumerate(lines):
        for pattern in patterns:
            m = re.search(rf"(?:{pattern})\s*[:：\-]?\s*(.*)$", line, re.I)
            if m and m.group(1).strip():
                return m.group(1).strip()
            if re.search(pattern, line, re.I) and i + 1 < len(lines):
                return lines[i + 1].strip()
    return None

def normalize_gender(value):
    if not value: return None
    v=value.strip().lower()
    if "পুরুষ" in value or v in {"male", "m"}: return "পুরুষ"
    if "মহিলা" in value or "নারী" in value or v in {"female", "f"}: return "মহিলা"
    return value.strip()

def parse_record(text):
    lines=[re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    data={k:value_after_label(lines,p) for k,p in LABELS.items()}
    data["gender"]=normalize_gender(data.get("gender"))
    raw_date=data.get("birth_date")
    if raw_date:
        for fmt in ("%d/%m/%Y","%d-%m-%Y","%d.%m.%Y"):
            try: data["birth_date"]=datetime.strptime(raw_date.strip(),fmt).date(); break
            except ValueError: pass
    data["raw_text"]=text
    return data

def split_voters(text):
    lines=text.splitlines(); chunks=[]; current=[]
    serial_re=re.compile(r"^\s*(?:ক্রমিক\s*)?\d+\s*$")
    for line in lines:
        if serial_re.match(line) and current:
            chunks.append("\n".join(current)); current=[line]
        else: current.append(line)
    if current: chunks.append("\n".join(current))
    return chunks or [text]

def process_pdf(file_path):
    results=[]; any_ocr=False
    with fitz.open(file_path) as doc:
        for page_number,page in enumerate(doc,1):
            text,ocr=extract_page(page); any_ocr |= ocr
            for chunk in split_voters(text):
                if len(re.sub(r"\s+", "", chunk)) < 15: continue
                record=parse_record(chunk); record.update({"page_number":page_number,"ocr_used":ocr,"confidence":0.75 if ocr else 0.95})
                results.append(record)
    return results, any_ocr, len(doc)
