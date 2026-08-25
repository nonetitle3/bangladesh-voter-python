import re
import unicodedata
from datetime import datetime

import fitz
from PIL import Image, ImageEnhance, ImageOps
import pytesseract

DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
BENGALI_CONSONANT = set("কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহড়ঢ়য়ৎ")
BENGALI_VOWEL_SIGNS = set("ািীুূৃেৈোৌ")
PREBASE_SIGNS = set("িীেৈ")


def normalize(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = text.replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def repair_bengali_matras(text):
    """Repair Bengali PDF/OCR visual-order corruption without guessing spelling.

    Examples:
      মািনক -> মানিক
      িময়া -> মিয়া
      জুেবদা -> জুবেদা
      েম -> মে

    A pre-base sign is moved only when the PDF has actually emitted it in a
    position that indicates corruption. Normal spellings such as বেগম are
    left untouched.
    """
    if not text:
        return ""

    chars = list(text)

    # A vowel sign at the beginning of a token may have been emitted before
    # its base consonant.
    out = []
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch in PREBASE_SIGNS and (i == 0 or chars[i - 1] in " \n\t,.;:()[]{}-/"):
            j = i + 1
            while j < len(chars) and chars[j] in PREBASE_SIGNS:
                j += 1
            if j < len(chars) and chars[j] in BENGALI_CONSONANT:
                out.append(chars[j])
                out.extend(chars[i:j])
                i = j + 1
                continue
        out.append(ch)
        i += 1

    # If a post-base sign is followed by a pre-base sign before the next
    # consonant, the pre-base sign normally belongs to that next consonant:
    # ম + া + ি + ন -> ম + া + ন + ি.
    chars = out
    out = []
    i = 0
    while i < len(chars):
        ch = chars[i]
        out.append(ch)

        if ch in BENGALI_CONSONANT:
            j = i + 1
            marks = []
            while j < len(chars) and chars[j] in BENGALI_VOWEL_SIGNS:
                marks.append(chars[j])
                j += 1

            pre = [m for m in marks if m in PREBASE_SIGNS]
            post = [m for m in marks if m not in PREBASE_SIGNS]

            if pre and post and j < len(chars) and chars[j] in BENGALI_CONSONANT:
                out.extend(post)
                out.append(chars[j])
                out.extend(pre)
                i = j + 1
                continue

        i += 1

    return "".join(out)


def repair(text):
    text = normalize(text)
    replacements = [
        ("Ïমাসাঃ", "মোসাঃ"), ("Ïমাঃ", "মোঃ"),
        ("Ïভাটার", "ভোটার"), ("Ïপেশা", "পেশা"), ("Ïপশা", "পেশা"),
        ("Ïজলা", "জেলা"), ("Ïউপেজলা", "উপজেলা"),
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


def clean(value):
    value = repair(value)
    return value.strip(" :-：,।\n") or None


def normalize_field(value, field):
    value = clean(value)
    if not value:
        return None
    value = repair_bengali_matras(value)
    value = re.sub(r"\s+", " ", value).strip()

    if field in {"name", "father_name", "mother_name"}:
        # Only structural/Unicode cleanup; do not guess personal names.
        value = value.replace("মোঃ", "মোঃ").replace("মোসাঃ", "মোসাঃ")
        value = value.replace("মােঃ", "মোঃ")
        value = value.replace("মি য়া", "মিয়া").replace("মি য়া", "মিয়া")
        value = re.sub(r"\s+([ািীুূৃেৈোৌ্য়ঁংঃ])", r"\1", value)

    elif field == "address":
        value = re.sub(r"\s*,\s*", ", ", value)
        value = value.replace("ইউনিয়ন", "ইউনিয়ন")
        value = value.replace("ওয়ার্ড", "ওয়ার্ড")
        value = value.replace("গ্রামঃ", "গ্রাম:")

    elif field == "occupation":
        value = value.replace("বËবসা", "ব্যবসা").replace("Řিমক", "শ্রমিক")
        value = value.replace("গৃহীণী", "গৃহিণী").replace("গৃহিনী", "গৃহিণী")
        value = value.replace("ছাÛ", "ছাত্র").replace("ছাÊ", "ছাত্র")

    elif field in {"district", "upazila", "union_name"}:
        value = value.replace("ময়মনিসংহ", "ময়মনসিংহ")
        value = value.replace("মু×াগাছা", "মুক্তাগাছা")
        value = value.replace("মু্ক্তাগাছা", "মুক্তাগাছা")
        value = value.replace("পাƁলীতলা", "পারুলীতলা")

    elif field == "voter_id":
        value = value.translate(DIGITS)
        value = re.sub(r"[^0-9]", "", value)

    return normalize(value)


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
    if stop_labels:
        stop = "|".join(stop_labels)
        pattern = rf"{label}\s*[:：]?\s*(.+?)(?=\s*(?:{stop})\s*[:：]|$)"
    else:
        pattern = rf"{label}\s*[:：]?\s*(.+)$"
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

        name = _field(block, ["নাম"], ["ভোটার(?:\s*নং)?", "পিতা", "মাতা", "পেশা", "জন্ম(?:\s*তারিখ)?", "ঠিকানা"], "name")
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
            result[field] = normalize_field(m.group(1), field) if field not in {"post_code", "ward"} else clean(m.group(1))
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
