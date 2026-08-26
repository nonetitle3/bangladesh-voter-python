import re
import unicodedata
from datetime import datetime

import fitz
from PIL import Image, ImageEnhance, ImageOps
import pytesseract

DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
PREBASE = set("িীেৈ")
VOWELS = set("ািীুূৃেৈোৌ")
CONSONANTS = set("কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহড়ঢ়য়ৎ")
SUSPICIOUS = set("ŐýƁƀƄƣËŘŞ×ĥėÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞß")


def normalize(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text)).replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def repair_bengali_matras(text):
    if not text:
        return ""
    chars, out, i = list(text), [], 0
    while i < len(chars):
        if chars[i] in PREBASE and (i == 0 or chars[i-1] in " \n\t,.;:()[]{}-/\\"):
            j = i + 1
            while j < len(chars) and chars[j] in PREBASE:
                j += 1
            if j < len(chars) and chars[j] in CONSONANTS:
                out.append(chars[j]); out.extend(chars[i:j]); i = j + 1; continue
        out.append(chars[i]); i += 1
    chars, out, i = out, [], 0
    while i < len(chars):
        ch = chars[i]; out.append(ch)
        if ch in CONSONANTS:
            j, marks = i + 1, []
            while j < len(chars) and chars[j] in VOWELS:
                marks.append(chars[j]); j += 1
            pre = [m for m in marks if m in PREBASE]; post = [m for m in marks if m not in PREBASE]
            if pre and post and j < len(chars) and chars[j] in CONSONANTS:
                out.extend(post); out.append(chars[j]); out.extend(pre); i = j + 1; continue
        i += 1
    return "".join(out)


def repair(text):
    text = normalize(text)
    replacements = {
        "শিŐী":"শিল্পী","মýুƁল":"মকবুল","Ïমাসাঃ":"মোসাঃ","Ïমাঃ":"মোঃ","Ïভাটার":"ভোটার",
        "Ïপেশা":"পেশা","Ïপশা":"পেশা","Ïজলা":"জেলা","Ïউপেজলা":"উপজেলা","Ïঘাগা":"ঘোগা",
        "Ïভাটার এলাকার নাম":"ভোটার এলাকার নাম","Ïভাটার এলাকার Ïকাড":"ভোটার এলাকার কোড","ÏপাŞেকাড":"পোস্টকোড",
        "Ïডাকঘর":"ডাকঘর","িপতা":"পিতা","িঠকানা":"ঠিকানা","মু×াগাছা":"মুক্তাগাছা",
        "পাƁলীতলা":"পারুলীতলা","পাƁলতলী":"পারুলতলী","পাƁলী তলা":"পারুলী তলা","ময়মনিসংহ":"ময়মনসিংহ",
        "জĥ তািরখ":"জন্ম তারিখ","উিėন":"উদ্দিন","উėীন":"উদ্দীন","চħ":"চন্দ্র","ƀবল":"সুবল",
        "Ƅƣর":"শুকুর","Ɓিকয়া":"রুকিয়া","Ɓƣমল":"রুকুমল","বËবসা":"ব্যবসা","Řিমক":"শ্রমিক",
        "Ïরেহনা":"রেহনা","Ïবগম":"বেগম","Ïহােসন":"হোসেন","Ïমাহা":"মোহা"
    }
    for old, new in replacements.items(): text = text.replace(old, new)
    return normalize(repair_bengali_matras(text.replace("Ő","ল্প").replace("ýুƁ","কবু").replace("Ï","").replace("×","ক্").replace("ĥ","ন্").replace("ė","দ্দ").replace("Î","র্")))


def clean(value):
    return repair(value).strip(" :-：,।\n") or None


def normalize_field(value, field):
    value = clean(value)
    if not value: return None
    value = re.sub(r"\s+", " ", repair_bengali_matras(value)).strip()
    if field in {"name","father_name","mother_name"}:
        value = value.replace("মোঃ","মোঃ").replace("মোসাঃ","মোসাঃ").replace("মােঃ","মোঃ").replace("মি য়া","মিয়া").replace("মি য়া","মিয়া")
        value = re.sub(r"\s+([ািীুূৃেৈোৌ্য়ঁংঃ])", r"\1", value)
    elif field == "address":
        value = re.sub(r"\s*,\s*", ", ", value).replace("ইউনিয়ন","ইউনিয়ন").replace("ওয়ার্ড","ওয়ার্ড").replace("গ্রামঃ","গ্রাম:")
    elif field in {"district","upazila","union_name"}:
        value = value.replace("ময়মনিসংহ","ময়মনসিংহ").replace("মু্ক্তাগাছা","মুক্তাগাছা").replace("ইউনিয়ন","ইউনিয়ন")
    elif field == "occupation":
        value = value.replace("গৃহীণী","গৃহিণী").replace("গৃহিনী","গৃহিণী").replace("ছাÛ","ছাত্র").replace("ছাÊ","ছাত্র")
    elif field == "voter_id":
        value = re.sub(r"[^0-9]", "", value.translate(DIGITS))
    elif field in {"ward","post_code"}:
        value = value.translate(DIGITS)
    return normalize(value)


def parse_date(value):
    m = re.search(r"([০-৯0-9]{1,2}[/-][০-৯0-9]{1,2}[/-][০-৯0-9]{4})", value or "")
    if not m: return None
    raw = m.group(1).translate(DIGITS)
    for fmt in ("%d/%m/%Y","%d-%m-%Y"):
        try: return datetime.strptime(raw, fmt).date()
        except ValueError: pass
    return None


def _field(block, labels, stops, field):
    label = "(?:" + "|".join(labels) + ")"
    stop = "|".join(stops)
    pattern = rf"{label}\s*[:：]?\s*(.+?)(?=\s*(?:{stop})\s*[:：]?|$)" if stop else rf"{label}\s*[:：]?\s*(.+)$"
    m = re.search(pattern, block, re.I | re.S)
    return normalize_field(m.group(1), field) if m else None


def native_records(page):
    raw = page.get_text("text") or ""
    if not raw: return []
    text = repair(raw)
    marker = re.compile(r"(?<![০-৯0-9])([০-৯0-9]{1,4})\s*\.\s*(?=(?:নাম|নামঃ|নাম:)\s*[:：]?)")
    starts = list(marker.finditer(text)) or list(re.finditer(r"(?<![০-৯0-9])([০-৯0-9]{1,4})\s*\.\s*", text))
    records = []
    for i, match in enumerate(starts):
        block = text[match.end():(starts[i+1].start() if i+1 < len(starts) else len(text))].strip()
        rec = {
            "serial_no": match.group(1).translate(DIGITS),
            "name": _field(block,["নাম"],["ভোটার(?:\s*নং)?","ভোটার\s*নম্বর","NID","Voter\s*ID","পিতা","মাতা","পেশা","জন্ম(?:\s*তারিখ)?","ঠিকানা"],"name"),
            "voter_id": _field(block,[r"ভোটার\s*নং",r"ভোটার\s*নম্বর","NID",r"Voter\s*ID"],["পিতা","মাতা","পেশা","জন্ম(?:\s*তারিখ)?","ঠিকানা"],"voter_id"),
            "father_name": _field(block,["পিতা",r"পিতার\s*নাম"],["মাতা","পেশা","জন্ম(?:\s*তারিখ)?","ঠিকানা"],"father_name"),
            "mother_name": _field(block,["মাতা",r"মাতার\s*নাম"],["পেশা","জন্ম(?:\s*তারিখ)?","ঠিকানা"],"mother_name"),
            "address": _field(block,["ঠিকানা"],[],"address"),
            "village": _field(block,["গ্রাম","গ্রাম/মহল্লা","গ্রাম/মহল্লার নাম"],["ওয়ার্ড","ওয়ার্ড","ইউনিয়ন","ইউনিয়ন","উপজেলা","জেলা"],"village"),
            "ward": _field(block,["ওয়ার্ড","ওয়ার্ড"],["ইউনিয়ন","ইউনিয়ন","উপজেলা","জেলা","ঠিকানা"],"ward"),
            "union_name": _field(block,["ইউনিয়ন","ইউনিয়ন"],["উপজেলা","জেলা","ঠিকানা"],"union_name"),
            "upazila": _field(block,["উপজেলা"],["জেলা","ঠিকানা"],"upazila"),
            "district": _field(block,["জেলা"],["ঠিকানা"],"district"),
            "occupation": _field(block,["পেশা"],["জন্ম(?:\s*তারিখ)?","ঠিকানা"],"occupation"),
            "birth_date": parse_date(block),
            "gender": _field(block,["লিঙ্গ","লিঙ্গঃ"],["পেশা","জন্ম(?:\s*তারিখ)?","ঠিকানা"],"gender"),
            "raw_text": block,
        }
        if any(rec[k] for k in ("name","voter_id","father_name","mother_name","address")): records.append(rec)
    return records


def location_metadata(doc):
    text = repair("\n".join((doc[i].get_text("text") or "") for i in range(min(2,len(doc)))))
    patterns = {
        "district":r"জেলা\s*[:：]?\s*(.+?)(?=\s+উপজেলা\s*[:：]|\n|$)",
        "upazila":r"উপজেলা\s*[:：]?\s*(.+?)(?=\s+(?:ইউনিয়ন|ইউনিয়ন)\s*[:：]|\n|$)",
        "union_name":r"(?:ইউনিয়ন|ইউনিয়ন)\s*[:：]?\s*(.+?)(?=\s+(?:ডাকঘর|পোস্টকোড|ভোটার এলাকার)\s*[:：]|\n|$)",
        "post_code":r"(?:পোস্টকোড|ভোটার এলাকার কোড)\s*[:：]?\s*([০-৯0-9]+)",
        "ward":r"(?:ওয়ার্ড|ওয়ার্ড|ওয়াড)\s*(?:নম্বর)?\s*[:：]?\s*([০-৯0-9]+)",
        "address":r"ভোটার এলাকার নাম\s*[:：]?\s*(.+?)(?=\n|$)"}
    out={}
    for field, pattern in patterns.items():
        m=re.search(pattern,text,re.I|re.S)
        if m: out[field]=normalize_field(m.group(1),field)
    return out


def ocr_fallback(page):
    pix=page.get_pixmap(dpi=160,alpha=False)
    image=Image.frombytes("RGB",[pix.width,pix.height],pix.samples)
    image=ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.25)
    text=pytesseract.image_to_string(image,lang="ben+eng",config="--psm 6")
    class OCRPage:
        def get_text(self,mode="text"): return text
    return native_records(OCRPage())


def has_suspicious_encoding(text):
    return bool(text) and any(ch in SUSPICIOUS for ch in text)


def records_have_encoding_corruption(records):
    return any(has_suspicious_encoding(r.get(f)) for r in records for f in ("name","father_name","mother_name","address","occupation"))


def process_pdf(file_path, progress_callback=None):
    results=[]; any_ocr=False
    def progress(page,total,stage,records):
        if progress_callback:
            try: progress_callback(page,total,stage,records)
            except Exception: pass
    with fitz.open(file_path) as doc:
        total=len(doc); location=location_metadata(doc)
        progress(min(2,total),total,"reading location pages",0)
        for page_number in range(3,total+1):
            progress(page_number,total,"extracting voter records",len(results))
            page=doc[page_number-1]
            # Text PDFs must stay on the native extraction path. Embedded font
            # detection is NOT a reason to OCR: a readable Unicode/text layer
            # can use embedded Bengali fonts and still be extracted correctly.
            page_records=native_records(page)
            page_used_ocr=False
            if not page_records:
                ocr_records=ocr_fallback(page)
                if ocr_records:
                    page_records=ocr_records; page_used_ocr=True; any_ocr=True
            for record in page_records:
                for field in ("district","upazila","union_name","ward","post_code"):
                    if not record.get(field) and location.get(field): record[field]=location[field]
                if not record.get("address") and location.get("address"): record["address"]=location["address"]
                record["page_number"]=page_number; record["ocr_used"]=page_used_ocr
                record["confidence"]=0.98 if not page_used_ocr else 0.75
                results.append(record)
            progress(page_number,total,"saving records",len(results))
    progress(total,total,"completed",len(results))
    return results,any_ocr,total
