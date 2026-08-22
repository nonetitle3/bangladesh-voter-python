import re
import unicodedata
from datetime import datetime

import fitz
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract

LABELS = {
    "voter_id": [r"ভোটার\s*(?:নং|নম্বর)", r"NID", r"Voter\s*ID"],
    "serial_no": [r"ক্রমিক", r"সিরিয়াল", r"সিরিয়াল", r"serial"],
    "name": [r"নাম", r"name"],
    "father_name": [r"পিতা", r"পিতার\s*নাম", r"father"],
    "mother_name": [r"মাতা", r"মাতার\s*নাম", r"mother"],
    "birth_date": [r"জন্ম\s*তারিখ", r"DOB", r"date\s*of\s*birth"],
    "occupation": [r"পেশা", r"occupation"],
    "gender": [r"লিঙ্গ", r"gender"],
    "address": [r"ঠিকানা", r"ঠিকানা\s*ঃ?", r"address"],
    "village": [r"গ্রাম", r"village"],
    "ward": [r"ওয়ার্ড", r"ওয়ার্ড", r"ওয়র্ড", r"ward"],
    "union_name": [r"ইউনিয়ন", r"ইউনিয়ন", r"union"],
    "upazila": [r"উপজেলা", r"upazila"],
    "district": [r"জেলা", r"district"],
    "division": [r"বিভাগ", r"division"],
    "post_code": [r"পোস্ট\s*কোড", r"পোস্টকোড", r"post\s*code"],
}
ALL_LABEL_PATTERNS = [p for patterns in LABELS.values() for p in patterns]
NEXT_LABEL_RE = re.compile(r"(?:" + "|".join(ALL_LABEL_PATTERNS) + r")\s*[:：-]?", re.I)
BENGALI_RE = re.compile(r"[\u0980-\u09FF]")
BENGALI_CONSONANT_RE = r"[\u0985-\u09B9\u09DC-\u09DF]"
DEPENDENT_VOWEL_RE = r"[\u09BF-\u09CC]"


def normalize_bengali(text):
    if not text:
        return text
    text = unicodedata.normalize("NFC", str(text))
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = text.replace("\u00a0", " ")

    # Tesseract sometimes emits a Bengali dependent vowel sign before the
    # consonant it belongs to. Example: জুেবদা -> জুবেদা. In valid Unicode
    # Bengali, the dependent sign follows its consonant, so this correction
    # is safe for these OCR ordering errors.
    for _ in range(3):
        fixed = re.sub(
            rf"({DEPENDENT_VOWEL_RE})({BENGALI_CONSONANT_RE})",
            r"\2\1",
            text,
        )
        fixed = re.sub(
            rf"({DEPENDENT_VOWEL_RE})([\u09CD])({BENGALI_CONSONANT_RE})",
            r"\2\3\1",
            fixed,
        )
        if fixed == text:
            break
        text = fixed

    # A common OCR artefact is a space inserted between a base letter and its
    # dependent vowel sign. Remove only spaces around Bengali combining marks.
    text = re.sub(rf"({BENGALI_CONSONANT_RE})\s+({DEPENDENT_VOWEL_RE})", r"\1\2", text)
    text = re.sub(rf"({DEPENDENT_VOWEL_RE})\s+({BENGALI_CONSONANT_RE})", r"\1\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def preprocess(image, threshold=175):
    image = ImageOps.grayscale(image)
    image = ImageEnhance.Contrast(image).enhance(1.6)
    image = ImageEnhance.Sharpness(image).enhance(1.25)
    image = image.filter(ImageFilter.SHARPEN)
    return image.point(lambda p: 255 if p > threshold else 0)


def native_text_is_good(text):
    """Use the PDF text layer when it is complete enough; this avoids slow OCR."""
    compact = re.sub(r"\s+", "", text or "")
    if len(compact) < 80:
        return False
    bengali = len(BENGALI_RE.findall(text or ""))
    if bengali < 15:
        return False
    labels_found = sum(
        1
        for p in (r"নাম", r"পিতা", r"মাতা", r"ঠিকানা", r"জেলা", r"উপজেলা")
        if re.search(p, text or "")
    )
    return labels_found >= 2


def ocr_quality(text):
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return -1
    bengali = len(BENGALI_RE.findall(text or ""))
    labels = sum(1 for p in (r"নাম", r"পিতা", r"মাতা", r"ঠিকানা", r"জেলা", r"উপজেলা", r"ইউনিয়ন", r"ওয়ার্ড") if re.search(p, text or ""))
    replacement = text.count("�")
    return min(len(compact), 500) + bengali * 3 + labels * 100 - replacement * 100


def extract_page(page):
    native = page.get_text("text") or ""

    if native_text_is_good(native):
        return normalize_bengali(native), False

    # 2x rendering is the normal pass for a good speed/accuracy balance.
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    original = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    candidates = []

    for config in ("--psm 6", "--psm 11"):
        text = pytesseract.image_to_string(original, lang="ben+eng", config=config) or ""
        candidates.append(text)

    best = max(candidates, key=ocr_quality, default="")

    # If both normal passes look weak, use a high-contrast retry. This keeps
    # ordinary pages fast while giving difficult scanned pages a stronger pass.
    if ocr_quality(best) < 260:
        enhanced = preprocess(original)
        for config in ("--psm 6", "--psm 11"):
            text = pytesseract.image_to_string(enhanced, lang="ben+eng", config=config) or ""
            candidates.append(text)
        best = max(candidates, key=ocr_quality, default=best)

    best = normalize_bengali(best)
    if len(re.sub(r"\s+", "", best)) >= 15:
        return best, True
    return normalize_bengali(native), False


def clean_field(value):
    if not value:
        return None
    value = normalize_bengali(value)
    value = re.sub(r"\s*[:：]\s*", ": ", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip(" -:：") or None


def _cut_at_next_label(value):
    if not value:
        return value
    m = NEXT_LABEL_RE.search(value)
    if m and m.start() > 0:
        value = value[:m.start()]
    return value


def value_after_label(lines, patterns, multiline=False):
    for i, line in enumerate(lines):
        for pattern in patterns:
            m = re.search(rf"(?:{pattern})\s*[:：\-]?\s*(.*)$", line, re.I)
            if m:
                first = clean_field(_cut_at_next_label(m.group(1)))
                if first:
                    if not multiline:
                        return first
                    values = [first]
                    j = i + 1
                    while j < len(lines):
                        candidate = lines[j].strip()
                        if not candidate or NEXT_LABEL_RE.search(candidate):
                            break
                        values.append(clean_field(candidate) or candidate)
                        j += 1
                    return clean_field(" ".join(values))

            if re.search(pattern, line, re.I) and i + 1 < len(lines):
                first = clean_field(_cut_at_next_label(lines[i + 1]))
                if not first:
                    continue
                if not multiline:
                    return first
                values = [first]
                j = i + 2
                while j < len(lines):
                    candidate = lines[j].strip()
                    if not candidate or NEXT_LABEL_RE.search(candidate):
                        break
                    values.append(clean_field(candidate) or candidate)
                    j += 1
                return clean_field(" ".join(values))
    return None


def normalize_gender(value):
    if not value:
        return None
    v = value.strip().lower()
    if "পুরুষ" in value or v in {"male", "m"}:
        return "পুরুষ"
    if "মহিলা" in value or "নারী" in value or v in {"female", "f"}:
        return "মহিলা"
    return clean_field(value)


def parse_record(text):
    text = normalize_bengali(text)
    lines = [normalize_bengali(re.sub(r"\s+", " ", x).strip()) for x in text.splitlines() if x.strip()]
    data = {key: value_after_label(lines, patterns, multiline=(key == "address")) for key, patterns in LABELS.items()}
    data["gender"] = normalize_gender(data.get("gender"))
    raw_date = data.get("birth_date")
    if raw_date:
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
            try:
                data["birth_date"] = datetime.strptime(raw_date.strip(), fmt).date()
                break
            except ValueError:
                pass
    data["raw_text"] = text
    return data


def split_voters(text):
    lines = text.splitlines()
    chunks, current = [], []
    serial_re = re.compile(r"^\s*(?:ক্রমিক\s*)?\d+\s*$")
    for line in lines:
        if serial_re.match(line) and current:
            chunks.append("\n".join(current)); current = [line]
        else:
            current.append(line)
    if current:
        chunks.append("\n".join(current))
    return chunks or [text]


def process_pdf(file_path):
    results, any_ocr = [], False
    with fitz.open(file_path) as doc:
        page_count = len(doc)
        for page_number, page in enumerate(doc, 1):
            text, ocr = extract_page(page)
            any_ocr = any_ocr or ocr
            for chunk in split_voters(text):
                if len(re.sub(r"\s+", "", chunk)) < 15:
                    continue
                record = parse_record(chunk)
                record.update({"page_number": page_number, "ocr_used": ocr, "confidence": 0.82 if ocr else 0.95})
                results.append(record)
    return results, any_ocr, page_count
