import re
import unicodedata
from datetime import datetime

import fitz
import numpy as np
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
MOJIBAKE_MARKERS = ("Ï", "Ɓ", "ė", "ĥ", "×", "Î", "Ð", "Ý", "�")


def normalize_bengali(text):
    if not text:
        return text
    text = unicodedata.normalize("NFC", str(text))
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = text.replace("\u00a0", " ")
    for _ in range(3):
        fixed = re.sub(rf"({DEPENDENT_VOWEL_RE})({BENGALI_CONSONANT_RE})", r"\2\1", text)
        fixed = re.sub(rf"({DEPENDENT_VOWEL_RE})([\u09CD])({BENGALI_CONSONANT_RE})", r"\2\3\1", fixed)
        if fixed == text:
            break
        text = fixed
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
    """Only use the PDF text layer when it is not a known font-encoding/mojibake export."""
    text = text or ""
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        return False
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 80:
        return False
    bengali = len(BENGALI_RE.findall(text))
    if bengali < 15:
        return False
    labels_found = sum(
        1 for p in (r"নাম", r"পিতা", r"মাতা", r"ঠিকানা", r"জেলা", r"উপজেলা")
        if re.search(p, text)
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


def _group_lines(values, gap=20):
    groups = []
    for value in values:
        value = int(value)
        if not groups or value > groups[-1][-1] + gap:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [int(sum(group) / len(group)) for group in groups if len(group) >= 2]


def _grid_bounds(image):
    """Detect the three-column/five-row voter boxes used by Bangladesh voter-list PDFs."""
    arr = np.asarray(image.convert("L"))
    h, w = arr.shape
    binary = arr < 180

    # Long horizontal borders around the voter cards.
    y_count = binary[:, int(w * 0.07):int(w * 0.96)].sum(axis=1)
    y_candidates = np.where(y_count > w * 0.75)[0]
    y_lines = _group_lines(y_candidates)
    if len(y_lines) < 6:
        return None
    # The last six long lines are normally the top/bottom borders of the 5 rows.
    y_lines = y_lines[-6:]

    # Long vertical borders around the three columns.
    y0, y1 = int(h * 0.15), int(h * 0.90)
    x_count = binary[y0:y1, :].sum(axis=0)
    x_candidates = np.where(x_count > (y1 - y0) * 0.65)[0]
    x_lines = _group_lines(x_candidates)
    if len(x_lines) < 4:
        return None
    # Merge double border lines that are only a few pixels apart.
    merged = []
    for x in x_lines:
        if not merged or x - merged[-1] > 35:
            merged.append(x)
        else:
            merged[-1] = int((merged[-1] + x) / 2)
    if len(merged) < 4:
        return None
    return merged[:4], y_lines


def _field_score(key, value):
    if not value:
        return -1
    value = str(value)
    bengali = len(BENGALI_RE.findall(value))
    digits = len(re.findall(r"\d", value))
    latin = len(re.findall(r"[A-Za-z]", value))
    score = bengali * 4 + min(len(value), 80) - latin * 3
    if key == "voter_id":
        score += digits * 8
    if key == "birth_date" and re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", value):
        score += 100
    return score


def _merge_candidate_records(records):
    if not records:
        return {}
    keys = set().union(*(record.keys() for record in records))
    merged = {}
    for key in keys:
        values = [record.get(key) for record in records if record.get(key)]
        if values:
            merged[key] = max(values, key=lambda value: _field_score(key, value))
    return merged


def _ocr_cell(image):
    candidates = []
    # PSM 4 preserves the form's lines well; PSM 11 recovers fields that PSM 4
    # occasionally misses. We only run the second pass when needed.
    first = pytesseract.image_to_string(image, lang="ben+eng", config="--psm 4") or ""
    candidates.append(first)
    parsed = [parse_record(first)] if first.strip() else []
    important = ("name", "voter_id", "father_name", "mother_name", "address")
    if any(not parsed[0].get(k) for k in important) if parsed else True:
        second = pytesseract.image_to_string(image, lang="ben+eng", config="--psm 11") or ""
        if second.strip():
            candidates.append(second)
            parsed.append(parse_record(second))
    merged = _merge_candidate_records(parsed)
    # Keep the best raw OCR text for diagnostics.
    if candidates:
        merged["raw_text"] = max(candidates, key=ocr_quality)
    return merged


def extract_grid_records(page):
    """OCR each voter box independently instead of OCRing the whole 3-column page.

    The source PDF in this project has a corrupted Bengali PDF text layer. The
    visible page itself is clean, but whole-page OCR mixes the three columns.
    Cell-level OCR preserves the correct name/father/mother/address relationship.
    """
    pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    bounds = _grid_bounds(image)
    if not bounds:
        return None
    x_lines, y_lines = bounds
    results = []
    for row in range(5):
        for col in range(3):
            left, right = x_lines[col] + 8, x_lines[col + 1] - 8
            top, bottom = y_lines[row] + 8, y_lines[row + 1] - 8
            if right <= left or bottom <= top:
                continue
            cell = image.crop((left, top, right, bottom))
            record = _ocr_cell(cell)
            if record.get("name") or record.get("voter_id"):
                record["ocr_used"] = True
                results.append(record)
    return results


def extract_page(page):
    native = page.get_text("text") or ""

    # These voter-list PDFs use a three-column card layout. If enough serial/name
    # markers exist, use cell OCR even though the PDF has a text layer.
    voter_markers = len(re.findall(r"\b\d{3}\.\s*নাম", normalize_bengali(native)))
    if voter_markers >= 3:
        grid = extract_grid_records(page)
        if grid:
            return grid, True

    if native_text_is_good(native):
        return [parse_record(normalize_bengali(native))], False

    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    original = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    candidates = []
    for config in ("--psm 6", "--psm 11"):
        text = pytesseract.image_to_string(original, lang="ben+eng", config=config) or ""
        candidates.append(text)
    best = max(candidates, key=ocr_quality, default="")
    if ocr_quality(best) < 260:
        enhanced = preprocess(original)
        for config in ("--psm 6", "--psm 11"):
            text = pytesseract.image_to_string(enhanced, lang="ben+eng", config=config) or ""
            candidates.append(text)
        best = max(candidates, key=ocr_quality, default=best)
    best = normalize_bengali(best)
    return [parse_record(best)], bool(best.strip())


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
            chunks.append("\n".join(current))
            current = [line]
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
            page_results, ocr = extract_page(page)
            any_ocr = any_ocr or ocr
            for record in page_results:
                if len(re.sub(r"\s+", "", record.get("raw_text") or "")) < 15:
                    continue
                record.update({"page_number": page_number, "ocr_used": ocr, "confidence": 0.86 if ocr else 0.95})
                results.append(record)
    return results, any_ocr, page_count
