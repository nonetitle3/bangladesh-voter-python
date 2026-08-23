import re
import unicodedata
from datetime import datetime

import fitz
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract
from pytesseract import Output

BENGALI_RE = re.compile(r"[\u0980-\u09FF]")
DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def normalize_bengali(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = text.replace("\u00a0", " ")
    for _ in range(3):
        fixed = re.sub(r"([\u09BF-\u09CC])([\u0985-\u09B9\u09DC-\u09DF])", r"\2\1", text)
        fixed = re.sub(r"([\u09BF-\u09CC])([\u09CD])([\u0985-\u09B9\u09DC-\u09DF])", r"\2\3\1", fixed)
        fixed = re.sub(r"([\u0985-\u09B9\u09DC-\u09DF])\s+([\u09BF-\u09CC])", r"\1\2", fixed)
        if fixed == text:
            break
        text = fixed
    return re.sub(r"[ \t]+", " ", text).strip()


def clean(value):
    value = normalize_bengali(value)
    value = re.sub(r"^[\s:：\-]+|[\s:：\-]+$", "", value)
    return value or None


def repair_pdf_text(text):
    """Repair the known broken glyph mapping used by this election PDF font.
    This is only a fallback; visual OCR is preferred for names."""
    if not text:
        return ""
    replacements = {
        "Ïভাটার": "ভোটার",
        "Ïমাঃ": "মোঃ",
        "Ïমাসাঃ": "মোসাঃ",
        "Ïপশা": "পেশা",
        "Ïজলা": "জেলা",
        "ÏপাŞেকাড": "পোস্টকোড",
        "Ïভাটার এলাকার": "ভোটার এলাকার",
        "Ïভাটার এলাকার নাম": "ভোটার এলাকার নাম",
        "Ïভাটার এলাকার নńর": "ভোটার এলাকার নম্বর",
        "িপতা": "পিতা",
        "Ïপশা": "পেশা",
        "িঠকানা": "ঠিকানা",
        "জĥ তািরখ": "জন্ম তারিখ",
        "মু×াগাছা": "মুক্তাগাছা",
        "পাƁলীতলা": "পারুলীতলা",
        "পাƁলী তলা": "পারুলী তলা",
        "পাƁলী তলা": "পারুলী তলা",
        "ময়মনিসংহ": "ময়মনসিংহ",
        "Ƅƣর": "শুকুর",
        "ƀনীল": "সুনীল",
        "চħ": "চন্দ্র",
        "উėীন": "উদ্দীন",
        "উিėন": "উদ্দিন",
        "Ɓল": "রুল",
        "Ɓ": "র",
        "×": "ক্",
        "ĥ": "ন্",
        "Ģ": "ন্",
        "Î": "ক",
        "Ï": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return normalize_bengali(text)


def preprocess(image):
    image = ImageOps.grayscale(image)
    image = ImageEnhance.Contrast(image).enhance(1.35)
    image = ImageEnhance.Sharpness(image).enhance(1.15)
    return image.filter(ImageFilter.SHARPEN)


def ocr_lines(image, config="--psm 6"):
    data = pytesseract.image_to_data(
        image,
        lang="ben+eng",
        config=config,
        output_type=Output.DICT,
    )
    tokens = []
    for i, raw in enumerate(data.get("text", [])):
        text = normalize_bengali(raw)
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1
        if conf < -1:
            continue
        tokens.append({
            "text": text,
            "left": int(data["left"][i]),
            "top": int(data["top"][i]),
            "width": int(data["width"][i]),
            "height": int(data["height"][i]),
            "conf": conf,
        })

    groups = []
    for token in sorted(tokens, key=lambda x: (x["top"], x["left"])):
        cy = token["top"] + token["height"] / 2
        target = None
        for group in groups:
            if abs(cy - group["cy"]) <= 18:
                target = group
                break
        if target is None:
            groups.append({"cy": cy, "items": [token]})
        else:
            target["items"].append(token)
            target["cy"] = sum(
                x["top"] + x["height"] / 2 for x in target["items"]
            ) / len(target["items"])

    return [
        " ".join(x["text"] for x in sorted(group["items"], key=lambda x: x["left"]))
        for group in sorted(groups, key=lambda x: x["cy"])
    ]


def after_label(text, labels):
    for label in labels:
        match = re.search(rf"(?:{label})\s*[:：\-]?\s*(.+)$", text, re.I)
        if match:
            return clean(match.group(1))
    return None


def parse_card(lines):
    lines = [clean(x) for x in lines if clean(x)]
    if not lines:
        return {}

    result = {
        "serial_no": None,
        "name": None,
        "voter_id": None,
        "father_name": None,
        "mother_name": None,
        "occupation": None,
        "birth_date": None,
        "address": None,
        "raw_text": "\n".join(lines),
    }

    first = lines[0]
    serial = re.search(r"([০-৯0-9]{3})\s*\.?", first)
    if serial:
        result["serial_no"] = serial.group(1)
    result["name"] = after_label(first, [r"নাম", r"name"])
    if not result["name"] and serial:
        result["name"] = clean(first[serial.end():])

    for line in lines[1:]:
        if not result["voter_id"]:
            result["voter_id"] = after_label(line, [r"ভোটার\s*(?:নং|নম্বর)", r"ভাটার\s*(?:নং|নম্বর)", r"NID", r"Voter\s*ID"])
            if not result["voter_id"]:
                m = re.search(r"[০-৯0-9]{10,}", line)
                if m:
                    result["voter_id"] = m.group(0)
        if not result["father_name"]:
            result["father_name"] = after_label(line, [r"পিতা", r"পতা", r"পিতার\s*নাম", r"father"])
        if not result["mother_name"]:
            result["mother_name"] = after_label(line, [r"মাতা", r"মাতার\s*নাম", r"mother", r"নীতা"])
        if not result["occupation"] or not result["birth_date"]:
            if re.search(r"পেশা|পশা|occupation", line, re.I):
                occ_line = re.sub(r"^(?:পেশা|পশা|occupation)\s*[:：-]?\s*", "", line, flags=re.I)
                dm = re.search(r"([০-৯0-9]{1,2}[\/-][০-৯0-9]{1,2}[\/-][০-৯0-9]{2,4})", occ_line)
                if dm:
                    raw_date = dm.group(1).translate(DIGITS)
                    for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
                        try:
                            result["birth_date"] = datetime.strptime(raw_date, fmt).date()
                            break
                        except ValueError:
                            pass
                    result["occupation"] = clean(occ_line[:dm.start()].rstrip(" ,।"))
                else:
                    result["occupation"] = clean(occ_line)
        if not result["address"]:
            result["address"] = after_label(line, [r"ঠিকানা", r"address"])

    return result


def find_grid(page):
    """Find the fixed 3-column x 5-row voter-card grid from its printed borders."""
    pix = page.get_pixmap(matrix=fitz.Matrix(3.5, 3.5), alpha=False)
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    gray = np.asarray(image.convert("L"))
    dark = gray < 180
    h, w = dark.shape

    # Horizontal border candidates.
    y_counts = dark.sum(axis=1)
    y_positions = np.where(y_counts > w * 0.55)[0]
    y_groups = []
    for y in y_positions:
        if not y_groups or y - y_groups[-1][-1] > 6:
            y_groups.append([int(y)])
        else:
            y_groups[-1].append(int(y))
    ys = [int(sum(g) / len(g)) for g in y_groups if len(g) >= 2]

    # Choose the six equally spaced borders below the header.
    best_y = None
    for i in range(len(ys) - 5):
        seq = ys[i:i + 6]
        gaps = [seq[j + 1] - seq[j] for j in range(5)]
        if min(gaps) > h * 0.07 and max(gaps) / min(gaps) < 1.25:
            if seq[0] > h * 0.12:
                best_y = seq
    if not best_y:
        # Known template fallback, expressed as proportions so resolution does not matter.
        best_y = [
            int(h * 0.192), int(h * 0.320), int(h * 0.456),
            int(h * 0.592), int(h * 0.727), int(h * 0.862),
        ]

    # Vertical border candidates within the card-grid height.
    x_counts = dark[best_y[0]:best_y[-1], :].sum(axis=0)
    x_positions = np.where(x_counts > (best_y[-1] - best_y[0]) * 0.55)[0]
    x_groups = []
    for x in x_positions:
        if not x_groups or x - x_groups[-1][-1] > 6:
            x_groups.append([int(x)])
        else:
            x_groups[-1].append(int(x))
    xs = [int(sum(g) / len(g)) for g in x_groups if len(g) >= 2]

    best_x = None
    for i in range(len(xs) - 3):
        seq = xs[i:i + 4]
        gaps = [seq[j + 1] - seq[j] for j in range(3)]
        if min(gaps) > w * 0.20 and max(gaps) / min(gaps) < 1.25:
            best_x = seq
            break
    if not best_x:
        best_x = [
            int(w * 0.081), int(w * 0.357),
            int(w * 0.637), int(w * 0.918),
        ]

    return image, best_x, best_y


def native_records(page):
    """Extract the vector text already embedded in digitally generated PDFs.
    It supplies exact field boundaries and is used as a fallback when OCR drops
    a line such as address or voter number."""
    text = page.get_text("text") or ""
    if not text:
        return {}
    starts = list(re.finditer(r"(?m)^\s*([০-৯0-9]{3})\.\s*নাম\s*:", text))
    records = {}
    for idx, match in enumerate(starts):
        end = starts[idx + 1].start() if idx + 1 < len(starts) else len(text)
        block = text[match.start():end]
        serial = match.group(1)
        rec = {"serial_no": serial}
        patterns = {
            "name": r"নাম\s*:\s*(.+)",
            "voter_id": r"ভোটার\s*নং\s*:\s*([০-৯0-9]+)",
            "father_name": r"পিতা\s*:\s*(.+)",
            "mother_name": r"মাতা\s*:\s*(.+)",
            "address": r"িঠকানা\s*:\s*(.+)",
            "address2": r"ঠিকানা\s*:\s*(.+)",
        }
        for key, pattern in patterns.items():
            m = re.search(pattern, block)
            if m:
                value = repair_pdf_text(m.group(1).strip())
                if key == "address2":
                    rec["address"] = value
                elif key != "address2":
                    rec[key] = value
        occ = re.search(r"Ïপশা\s*:\s*(.+)", block)
        if occ:
            line = repair_pdf_text(occ.group(1))
            dm = re.search(r"([০-৯0-9]{1,2}[\/-][০-৯0-9]{1,2}[\/-][০-৯0-9]{2,4})", line)
            if dm:
                raw_date = dm.group(1).translate(DIGITS)
                for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
                    try:
                        rec["birth_date"] = datetime.strptime(raw_date, fmt).date()
                        break
                    except ValueError:
                        pass
                rec["occupation"] = clean(line[:dm.start()].rstrip(" ,।"))
            else:
                rec["occupation"] = clean(line)
        records[serial] = rec
    return records


def ocr_card(cell):
    candidates = []
    for config in ("--psm 6", "--psm 11"):
        lines = ocr_lines(cell, config)
        candidates.append(lines)
    enhanced = preprocess(cell)
    candidates.append(ocr_lines(enhanced, "--psm 6"))

    parsed = [parse_card(lines) for lines in candidates]
    # Prefer the candidate with the most useful fields, while preserving the
    # PSM 6 result for the normal six-line card layout.
    return max(parsed, key=lambda r: sum(bool(r.get(k)) for k in (
        "serial_no", "name", "voter_id", "father_name", "mother_name",
        "occupation", "birth_date", "address"
    )), default={})


def extract_cards(page):
    image, xs, ys = find_grid(page)
    native = native_records(page)
    records = []

    for row in range(5):
        for col in range(3):
            left = xs[col] + 10
            right = xs[col + 1] - 10
            top = ys[row] + 10
            bottom = ys[row + 1] - 10
            if right <= left or bottom <= top:
                continue

            cell = image.crop((left, top, right, bottom))
            rec = ocr_card(cell)
            serial = rec.get("serial_no")

            # If OCR mangles the three-digit serial, use the vector record in
            # this grid position. Pages in this voter-list are ordered row-wise.
            ordered_native = list(native.values())
            pos = row * 3 + col
            if pos < len(ordered_native):
                fallback = ordered_native[pos]
                if not serial:
                    serial = fallback.get("serial_no")
                for key, value in fallback.items():
                    if not rec.get(key) and value:
                        rec[key] = value

            if not rec.get("name") and not rec.get("voter_id"):
                continue
            if serial:
                rec["serial_no"] = serial
            rec["ocr_used"] = True
            records.append(rec)

    return records


def location_metadata(doc):
    out = {}
    text_parts = []
    for i in range(min(2, len(doc))):
        text = doc[i].get_text("text") or ""
        if text:
            text_parts.append(repair_pdf_text(text))
        else:
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            text_parts.append(pytesseract.image_to_string(image, lang="ben+eng", config="--psm 6"))
    text = normalize_bengali("\n".join(text_parts))
    patterns = {
        "district": r"জেলা\s*[:：]?\s*([^\n]+)",
        "upazila": r"উপজেলা(?:/থানা)?\s*[:：]?\s*([^\n]+)",
        "union_name": r"ইউনিয়ন[^\n]*?[:：]\s*([^\n]+)",
        "ward": r"ওয়ার্ড\s*নম্বর[^\n]*?[:：]\s*([০-৯0-9]+)",
        "post_code": r"পোস্টকোড\s*[:：]?\s*([০-৯0-9]+)",
        "division": r"অঞ্চল\s*[:：]?\s*([^\n]+)",
        "address": r"ভোটার এলাকার নাম\s*[:：]?\s*([^\n]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if match:
            value = clean(match.group(1))
            if value:
                out[key] = value
    return out


def apply_location(record, location):
    for key in ("district", "upazila", "union_name", "ward", "post_code", "division"):
        if not record.get(key) and location.get(key):
            record[key] = location[key]
    return record


def process_pdf(file_path, progress_callback=None):
    results = []
    any_ocr = False

    def progress(page, total, stage, records=0):
        if progress_callback:
            try:
                progress_callback(page, total, stage, records)
            except Exception:
                pass

    with fitz.open(file_path) as doc:
        total = len(doc)
        progress(0, total, "reading PDF", 0)
        location = location_metadata(doc)
        progress(min(2, total), total, "reading location pages", 0)

        for page_number in range(3, total + 1):
            progress(page_number, total, "OCR voter cards", len(results))
            page_records = extract_cards(doc[page_number - 1])
            for record in page_records:
                if not record.get("name") and not record.get("voter_id"):
                    continue
                record = apply_location(record, location)
                record.update({
                    "page_number": page_number,
                    "ocr_used": True,
                    "confidence": 0.90,
                })
                results.append(record)
            any_ocr = True
            progress(page_number, total, "saving records", len(results))

    progress(total, total, "completed", len(results))
    return results, any_ocr, total
