import re
import unicodedata
from datetime import datetime

import fitz
from PIL import Image, ImageEnhance, ImageOps
import pytesseract

DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
BENGALI = re.compile(r"[\u0980-\u09FF]")


def normalize(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def repair(text):
    text = normalize(text)
    # The supplied voter-list PDF uses a broken Bengali ToUnicode mapping.
    # Repair only mappings that are known from this document; do not blindly
    # replace arbitrary Bengali characters because that would damage names.
    replacements = [
        ("Ïমাসাঃ", "মোসাঃ"), ("Ïমাঃ", "মোঃ"),
        ("Ïভাটার", "ভোটার"), ("Ïপশা", "পেশা"),
        ("Ïজলা", "জেলা"), ("Ïউপেজলা", "উপজেলা"),
        ("Ïঘাগা", "ঘোগা"), ("Ïভাটার এলাকার নাম", "ভোটার এলাকার নাম"),
        ("Ïভাটার এলাকার Ïকাড", "ভোটার এলাকার কোড"),
        ("ÏপাŞেকাড", "পোস্টকোড"), ("িপতা", "পিতা"),
        ("িঠকানা", "ঠিকানা"), ("মু×াগাছা", "মুক্তাগাছা"),
        ("পাƁলীতলা", "পারুলীতলা"), ("পাƁলী তলা", "পারুলী তলা"),
        ("ময়মনিসংহ", "ময়মনসিংহ"), ("জĥ তািরখ", "জন্ম তারিখ"),
        ("উিėন", "উদ্দিন"), ("উėীন", "উদ্দীন"),
        ("চħ", "চন্দ্র"), ("ƀবল", "সুবল"),
        ("Ƅƣর", "শুকুর"), ("Ɓিকয়া", "রুকিয়া"),
        ("রিব", "রবি"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    # Common mojibake marks occurring inside Bengali names.
    text = text.replace("Ï", "")
    text = text.replace("×", "ক্")
    text = text.replace("ĥ", "ন্")
    text = text.replace("ė", "দ্দ")
    return normalize(text)


def clean(value):
    value = repair(value)
    return value.strip(" :-：,।") or None


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


def native_records(page):
    """Read digitally embedded voter text directly. This PDF is not a pure
    scanned PDF: every voter card contains selectable text. This path is the
    primary extractor and therefore avoids OCR coordinate errors entirely."""
    text = page.get_text("text") or ""
    if not text:
        return []
    text = text.replace("\r", "\n")
    # Records are explicitly printed as 001. নাম: ... through 015. নাম: ...
    starts = list(re.finditer(r"(?m)^\s*([০-৯0-9]{3})\s*\.\s*নাম\s*:", text))
    if not starts:
        return []
    records = []
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        block = text[match.start():end]
        serial = match.group(1)
        rec = {
            "serial_no": serial,
            "name": None,
            "voter_id": None,
            "father_name": None,
            "mother_name": None,
            "occupation": None,
            "birth_date": None,
            "address": None,
            "raw_text": repair(block),
        }

        patterns = {
            "name": r"নাম\s*:\s*([^\n]+)",
            "voter_id": r"(?:Ï)?ভোটার\s*নং\s*:\s*([০-৯0-9]+)",
            "father_name": r"(?:ি)?পিতা\s*:\s*([^\n]+)",
            "mother_name": r"মাতা\s*:\s*([^\n]+)",
            "address": r"(?:ি)?ঠ?িকানা\s*:\s*([^\n]+)",
        }
        for field, pattern in patterns.items():
            m = re.search(pattern, block, re.I)
            if m:
                rec[field] = clean(m.group(1))

        # Occupation and birth date share one printed line.
        m = re.search(r"(?:Ï)?পেশা\s*:\s*([^\n]+)", block, re.I)
        if m:
            line = repair(m.group(1))
            rec["birth_date"] = parse_date(line)
            dm = re.search(r"[০-৯0-9]{1,2}[/-][০-৯0-9]{1,2}[/-][০-৯0-9]{4}", line)
            rec["occupation"] = clean(line[:dm.start()] if dm else line)

        if rec["name"] or rec["voter_id"]:
            records.append(rec)
    return records


def location_metadata(doc):
    text = ""
    for i in range(min(2, len(doc))):
        text += "\n" + (doc[i].get_text("text") or "")
    text = repair(text)
    patterns = {
        "district": r"জেলা\s*:\s*([^\s]+)",
        "upazila": r"উপজেলা\s*:\s*([^\s]+)",
        "union_name": r"ইউনিয়ন\s*:\s*([^\s]+)",
        "post_code": r"পোস্টকোড\s*:\s*([০-৯0-9]+)",
        "ward": r"(?:ওয়ার্ড|ওয়াডÎ)\s*(?:নম্বর)?\s*[:]?\s*([০-৯0-9]+)",
        "address": r"ভোটার এলাকার নাম\s*:\s*([^\n]+)",
    }
    result = {}
    for field, pattern in patterns.items():
        m = re.search(pattern, text, re.I)
        if m:
            result[field] = clean(m.group(1))
    return result


def ocr_fallback(page):
    """Fallback for scanned pages that contain no embedded voter text."""
    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    image = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.4)
    text = pytesseract.image_to_string(image, lang="ben+eng", config="--psm 6")
    text = normalize(text)
    starts = list(re.finditer(r"(?m)^\s*([০-৯0-9]{3})\s*\.\s*", text))
    records = []
    for i, match in enumerate(starts):
        block = text[match.start():starts[i+1].start() if i + 1 < len(starts) else len(text)]
        lines = [clean(x) for x in block.splitlines() if clean(x)]
        rec = {"serial_no": match.group(1), "name": None, "voter_id": None,
               "father_name": None, "mother_name": None, "occupation": None,
               "birth_date": None, "address": None, "raw_text": repair(block)}
        for line in lines:
            for field, label in (("name", "নাম"), ("voter_id", "ভোটার নং"),
                                 ("father_name", "পিতা"), ("mother_name", "মাতা"),
                                 ("address", "ঠিকানা")):
                if line.startswith(label + ":") and not rec[field]:
                    rec[field] = clean(line.split(":", 1)[1])
            if "পেশা:" in line:
                value = line.split("পেশা:", 1)[1]
                rec["birth_date"] = parse_date(value)
                dm = re.search(r"[০-৯0-9]{1,2}[/-][০-৯0-9]{1,2}[/-][০-৯0-9]{4}", value)
                rec["occupation"] = clean(value[:dm.start()] if dm else value)
        if rec["name"] or rec["voter_id"]:
            records.append(rec)
    return records


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
        progress(2 if total >= 2 else total, total, "reading location pages", 0)

        for page_number in range(3, total + 1):
            progress(page_number, total, "extracting voter records", len(results))
            page = doc[page_number - 1]

            # PRIMARY: exact text extraction. For this PDF this should return
            # 15 records per voter page and must never depend on OCR borders.
            page_records = native_records(page)
            if not page_records:
                page_records = ocr_fallback(page)
                any_ocr = True

            for record in page_records:
                for field in ("district", "upazila", "union_name", "ward", "post_code"):
                    if not record.get(field) and location.get(field):
                        record[field] = location[field]
                record["page_number"] = page_number
                record["ocr_used"] = any_ocr
                record["confidence"] = 0.98 if not any_ocr else 0.75
                results.append(record)
            progress(page_number, total, "saving records", len(results))

    progress(total, total, "completed", len(results))
    return results, any_ocr, total
