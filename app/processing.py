import re
import unicodedata
from datetime import datetime

import fitz
from PIL import Image, ImageEnhance, ImageOps
import pytesseract

DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
BENGALI_CONSONANT = r"[\u0995-\u09B9\u09DC-\u09DF\u09F0-\u09FA]"


def normalize(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = text.replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def repair_bengali_matras(text):
    if not text:
        return ""
    for _ in range(3):
        text = re.sub(
            r"([\u09BE-\u09CC])([\u09BF\u09C0])(" + BENGALI_CONSONANT + r")",
            r"\1\3\2", text)
        text = re.sub(
            r"(^|[\s(\[\{\"'।,;:/-])([\u09BF\u09C0\u09C7\u09C8])(" + BENGALI_CONSONANT + r")",
            r"\1\3\2", text)
    return text


def repair(text):
    text = normalize(text)
    replacements = [
        ("Ïমাসাঃ", "মোসাঃ"), ("Ïমাঃ", "মোঃ"),
        ("Ïভাটার", "ভোটার"), ("Ïপেশা", "পেশা"), ("Ïপশা", "পেশা"),
        ("Ïজলা", "জেলা"), ("Ïউপেজলা", "উপজেলা"), ("Ïউপেজলা", "উপজেলা"),
        ("Ïঘাগা", "ঘোগা"), ("Ïভাটার এলাকার নাম", "ভোটার এলাকার নাম"),
        ("Ïভাটার এলাকার Ïকাড", "ভোটার এলাকার কোড"),
        ("ÏপাŞেকাড", "পোস্টকোড"), ("Ïডাকঘর", "ডাকঘর"),
        ("িপতা", "পিতা"), ("িঠকানা", "ঠিকানা"),
        ("মু×াগাছা", "মুক্তাগাছা"), ("পাƁলীতলা", "পারুলীতলা"),
        ("পাƁলতলী", "পারুলতলী"), ("পাƁলী তলা", "পারুলী তলা"),
        ("ময়মনিসংহ", "ময়মনসিংহ"), ("জĥ তািরখ", "জন্ম তারিখ"),
        ("উিėন", "উদ্দিন"), ("উėীন", "উদ্দীন"), ("চħ", "চন্দ্র"),
        ("ƀবল", "সুবল"), ("Ƅƣর", "শুকুর"), ("Ɓিকয়া", "রুকিয়া"),
        ("Ɓƣমল", "রুকুমল"), ("বËবসা", "ব্যবসা"), ("Řিমক", "শ্রমিক"),
        ("Ïরেহনা", "রেহনা"), ("Ïবগম", "বেগম"), ("Ïহােসন", "হোসেন"),
        ("Ïমাহা", "মোহা"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    text = text.replace("Ï", "")
    text = text.replace("×", "ক্")
    text = text.replace("ĥ", "ন্")
    text = text.replace("ė", "দ্দ")
    text = text.replace("Î", "র্")
    return normalize(repair_bengali_matras(text))


# Field-specific normalization. These rules only repair character/format
# corruption. They deliberately do NOT invent a person's spelling from a
# dictionary, because a unique Bengali name must be preserved exactly.
def normalize_field(value, field):
    value = clean(value)
    if not value:
        return None
    value = normalize(repair_bengali_matras(value))

    if field in {"name", "father_name", "mother_name"}:
        value = re.sub(r"\s+", " ", value)
        value = value.replace("মিঃ", "মিঃ").replace("মোঃ", "মোঃ")
        value = value.replace("মোসাঃ", "মোসাঃ")
        value = value.replace("মােঃ", "মোঃ")
        # OCR/PDF extraction often separates the y-phala sequence.
        value = value.replace("মি য়া", "মিয়া").replace("মি য়া", "মিয়া")
        value = re.sub(r"\s+([ািীুূৃেৈোৌ্য়ঁংঃ])", r"\1", value)

    elif field == "address":
        value = re.sub(r"\s*,\s*", ", ", value)
        value = re.sub(r"\s+", " ", value)
        value = value.replace("ইউনিয়ন", "ইউনিয়ন")
        value = value.replace("ওয়ার্ড", "ওয়ার্ড")
        value = value.replace("গ্রামঃ", "গ্রাম:").replace("গ্রাম-", "গ্রাম-")

    elif field == "occupation":
        value = re.sub(r"\s+", " ", value)
        occupation_map = {
            "বËবসা": "ব্যবসা", "Řিমক": "শ্রমিক", "গৃহীণী": "গৃহিণী",
            "গৃহিনী": "গৃহিণী", "ছাÛ": "ছাত্র", "ছাÊ": "ছাত্র",
        }
        for old, new in occupation_map.items():
            value = value.replace(old, new)

    elif field in {"district", "upazila", "union_name"}:
        value = re.sub(r"\s+", " ", value)
        location_map = {
            "ময়মনিসংহ": "ময়মনসিংহ", "ময়মনসিংহ": "ময়মনসিংহ",
            "মু×াগাছা": "মুক্তাগাছা", "মু্ক্তাগাছা": "মুক্তাগাছা",
            "পারুলীতলা": "পারুলীতলা", "পাƁলীতলা": "পারুলীতলা",
        }
        for old, new in location_map.items():
            value = value.replace(old, new)

    elif field == "voter_id":
        value = value.translate(DIGITS)
        value = re.sub(r"[^0-9]", "", value)

    return normalize(value)


def clean(value):
    value = repair(value)
    return value.strip(" :-：,।\n") or None


def parse_date(value):
    m = re.search(r"([০-৯0-9]{1,2}[/-][০-৯0-9]{1,2}[/-][০-৯0-9]{4})", value or "")
    if not m:
        return None
    raw = m.group(1).translate(DIGITS)
    for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def _field(block, labels, stop_labels, field_name):
    label = "(?:" + "|".join(labels) + ")"
    stop = "|".join(stop_labels)
    pattern = rf"{label}\s*[:：]?\s*(.+?)(?=\s*(?:{stop})\s*[:：]|$)" if stop else rf"{label}\s*[:：]?\s*(.+)$"
    m = re.search(pattern, block, flags=re.I | re.S)
    return normalize_field(m.group(1), field_name) if m else None


def native_records(page):
    raw = page.get_text("text") or ""
    if not raw:
        return []
    text = repair(raw)
    starts = list(re.finditer(r"(?m)(?:^|\n)\s*([০-৯0-9]{3})\s*\.\s*", text))
    if not starts:
        return []

    records = []
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        block = text[match.end():end].strip()
        serial = match.group(1)
        stop = ["নাম", "ভোটার(?:\s*নং)?", "পিতা", "মাতা", "পেশা", "জন্ম(?:\s*তারিখ)?", "ঠিকানা"]

        name = _field(block, ["নাম"], stop[1:], "name")
        voter_id = _field(block, [r"ভোটার\s*নং", r"ভোটার\s*নম্বর", "NID", r"Voter\s*ID"], ["পিতা", "মাতা", "পেশা", "জন্ম(?:\s*তারিখ)?", "ঠিকানা"], "voter_id")
        father = _field(block, ["পিতা", r"পিতার\s*নাম"], ["মাতা", "পেশা", "জন্ম(?:\s*তারিখ)?", "ঠিকানা"], "father_name")
        mother = _field(block, ["মাতা", r"মাতার\s*নাম"], ["পেশা", "জন্ম(?:\s*তারিখ)?", "ঠিকানা"], "mother_name")
        address = _field(block, ["ঠিকানা"], [], "address")

        occupation = None
        birth_date = None
        occ = re.search(r"পেশা\s*[:：]?\s*(.+?)(?=\s*(?:জন্ম\s*তারিখ|ঠিকানা)\s*[:：]|$)", block, flags=re.I | re.S)
        if occ:
            line = normalize_field(occ.group(1), "occupation") or ""
            birth_date = parse_date(line)
            dm = re.search(r"[০-৯0-9]{1,2}[/-][০-৯0-9]{1,2}[/-][০-৯0-9]{4}", line)
            occupation = normalize_field(line[:dm.start()] if dm else line, "occupation")
        if not birth_date:
            birth_date = parse_date(block)

        rec = {
            "serial_no": serial,
            "name": name,
            "voter_id": voter_id,
            "father_name": father,
            "mother_name": mother,
            "occupation": occupation,
            "birth_date": birth_date,
            "address": address,
            "raw_text": block,
        }
        if any(rec[k] for k in ("name", "voter_id", "father_name", "mother_name", "address")):
            records.append(rec)
    return records


def location_metadata(doc):
    text = repair("\n".join((doc[i].get_text("text") or "") for i in range(min(2, len(doc)))))
    result = {}
    patterns = {
        "district": r"জেলা\s*[:：]?\s*(.+?)(?=\s+উপজেলা\s*[:：]|\n|$)",
        "upazila": r"উপজেলা\s*[:：]?\s*(.+?)(?=\s+ইউনিয়ন\s*[:：]|\n|$)",
        "union_name": r"ইউনিয়ন\s*[:：]?\s*(.+?)(?=\s+(?:ডাকঘর|পোস্টকোড|ভোটার এলাকার)\s*[:：]|\n|$)",
        "post_code": r"(?:পোস্টকোড|ভোটার এলাকার কোড)\s*[:：]?\s*([০-৯0-9]+)",
        "ward": r"(?:ওয়ার্ড|ওয়াড)\s*(?:নম্বর)?\s*[:：]?\s*([০-৯0-9]+)",
        "address": r"ভোটার এলাকার নাম\s*[:：]?\s*(.+?)(?=\n|$)",
    }
    for field, pattern in patterns.items():
        m = re.search(pattern, text, flags=re.I | re.S)
        if m:
            result[field] = normalize_field(m.group(1), field) if field != "post_code" and field != "ward" else clean(m.group(1))
    return result


def ocr_fallback(page):
    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    image = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.4)
    text = pytesseract.image_to_string(image, lang="ben+eng", config="--psm 6")
    class OCRPage:
        def get_text(self, mode="text"):
            return text
    return native_records(OCRPage())


def process_pdf(file_path, progress_callback=None):
    results = []
    any_ocr = False

    def progress(page, total, stage, records):
        if progress_callback:
            try:
                progress_callback(page, total, stage, records)
            except Exception:
                pass

    with fitz.open(file_path) as doc:
        total = len(doc)
        location = location_metadata(doc)
        progress(min(2, total), total, "reading location pages", 0)
        for page_number in range(3, total + 1):
            progress(page_number, total, "extracting voter records", len(results))
            page = doc[page_number - 1]
            page_records = native_records(page)
            if not page_records:
                page_records = ocr_fallback(page)
                any_ocr = True
            for record in page_records:
                for field in ("district", "upazila", "union_name", "ward", "post_code"):
                    if not record.get(field) and location.get(field):
                        record[field] = location[field]
                if not record.get("address") and location.get("address"):
                    record["address"] = location["address"]
                record["page_number"] = page_number
                record["ocr_used"] = any_ocr
                record["confidence"] = 0.98 if not any_ocr else 0.75
                results.append(record)
            progress(page_number, total, "saving records", len(results))
    progress(total, total, "completed", len(results))
    return results, any_ocr, total
