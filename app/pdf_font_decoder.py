"""Font-aware native PDF text decoder for Bangladesh voter PDFs.

The source PDF uses an embedded Bangla Type0 font whose ToUnicode map does not
cover many shaped/conjunct glyphs. We recover those glyphs from the PDF text
trace while preserving PyMuPDF's native block/line ordering. This avoids
full-page OCR for a text-based PDF.
"""
import re
import fitz
from . import processing as base

# Glyph/shape mappings verified against the embedded JHXTEX+Bangla font and
# rendered pages from the supplied voter PDF. These are glyph mappings, not
# voter-name spelling guesses.
GLYPH_MAP = {
    1: " ", 140: "ন্", 152: "হ", 203: "্য", 206: "র্", 207: "",
    215: "ক্ত", 216: "ক্র", 217: "ক্ষ", 225: "ত্র", 229: "গ্র", 234: "ু",
    239: "চ্ছ", 245: "জ্জ", 251: "ঞ্চ", 253: "ঞ্জ", 259: "ড্র", 275: "প্র",
    276: "ত্র", 279: "দ্দ", 290: "ন্ত", 292: "ন্দ", 293: "ন্ম", 295: "ন্দ্র",
    296: "ন্ন", 297: "দ্দ", 303: "ন্য", 306: "ড়", 308: "প্র", 314: "ব্দ",
    316: "ম্ম", 317: "ব্র", 322: "ন্দ", 324: "ম্ব", 327: "ম্ম", 332: "ল্ল",
    336: "ল্প", 347: "জ", 349: "ষ্ণ", 350: "স্ট", 354: "স্ট্র", 361: "স্ত",
    369: "স্ত্র", 381: "ন্ত", 383: "নু", 384: "সু", 385: "রু", 387: "দু",
    388: "শ", 419: "কু",
}

_BAD_EXTRACTION = set("ŐýƁƀƄƣËŘŞ×ĥėÎÏÐÑÒÓÔÕÖØÙÚÜÝÞß�ħĔĨĴĢńįĺăſû")


def decoded_text(page):
    """Decode custom glyphs while retaining the PDF's native line structure."""
    origin_to_gid = {}
    for trace in page.get_texttrace():
        for ch in trace.get("chars", []):
            origin_to_gid[tuple(round(v, 3) for v in ch[2])] = ch[1]

    lines = []
    for block in page.get_text("rawdict").get("blocks", []):
        for line in block.get("lines", []):
            out = []
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    origin = tuple(round(v, 3) for v in ch["origin"])
                    gid = origin_to_gid.get(origin)
                    value = ch.get("c", "")
                    # A normal Bengali character can share the same origin with
                    # a shaped glyph (e.g. ু + a conjunct). Preserve the real
                    # Bengali character; replace only the corrupt extraction
                    # character using the glyph ID.
                    if gid in GLYPH_MAP and value in _BAD_EXTRACTION:
                        value = GLYPH_MAP[gid]
                    out.append(value)
            lines.append("".join(out))
    return "\n".join(lines)


def _repair(text):
    text = base.repair(text)
    # Structural label fixes only; do not rewrite voter names from a dictionary.
    text = text.replace("ভাটার", "ভোটার").replace("পশা", "পেশা")
    # Some PDFs put a pre-base mark after an independent Bengali vowel.
    text = re.sub(
        r"([অআইঈউঊঋএঐওঔ])([িীেৈ])([ক-হড়ঢ়য়ৎ](?:্[ক-হড়ঢ়য়ৎ])*)",
        r"\1\3\2",
        text,
    )
    return text


def native_records(page):
    text = _repair(decoded_text(page))
    starts = list(re.finditer(r"(?m)(?:^|\n)\s*([০-৯0-9]{3})\s*\.\s*", text))
    records = []
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        block = text[match.end():end].strip()
        serial = match.group(1)

        def fld(labels, stops, field_name):
            return base._field(block, labels, stops, field_name)

        record = {
            "serial_no": serial,
            "name": fld(["নাম"], [r"ভোটার(?:\s*নং)?", "পিতা", "মাতা", "পেশা", r"জন্ম(?:\s*তারিখ)?", "ঠিকানা"], "name"),
            "voter_id": fld([r"ভোটার\s*নং", r"ভোটার\s*নম্বর", "NID", r"Voter\s*ID"], ["পিতা", "মাতা", "পেশা", r"জন্ম(?:\s*তারিখ)?", "ঠিকানা"], "voter_id"),
            "father_name": fld(["পিতা", r"পিতার\s*নাম"], ["মাতা", "পেশা", r"জন্ম(?:\s*তারিখ)?", "ঠিকানা"], "father_name"),
            "mother_name": fld(["মাতা", r"মাতার\s*নাম"], ["পেশা", r"জন্ম(?:\s*তারিখ)?", "ঠিকানা"], "mother_name"),
            "address": fld(["ঠিকানা"], [], "address"),
            "occupation": None,
            "birth_date": base.parse_date(block),
            "raw_text": block,
        }

        occ = re.search(r"পেশা\s*[:：]?\s*(.+?)(?=\s*(?:জন্ম\s*তারিখ|ঠিকানা)\s*[:：]|$)", block, re.I | re.S)
        if occ:
            line = base.normalize_field(occ.group(1), "occupation") or ""
            dm = re.search(r"[০-৯0-9]{1,2}[/-][০-৯0-9]{1,2}[/-][০-৯0-9]{4}", line)
            record["birth_date"] = base.parse_date(line) or record["birth_date"]
            record["occupation"] = base.normalize_field(line[:dm.start()] if dm else line, "occupation")

        if any(record[k] for k in ("name", "voter_id", "father_name", "mother_name", "address")):
            records.append(record)
    return records


def _location(doc):
    text = _repair("\n".join(decoded_text(doc[i]) for i in range(min(2, len(doc)))))
    text = text.replace("ময়মনিসংহ", "ময়মনসিংহ").replace("ময়মনিসংহ", "ময়মনসিংহ")
    result = {}
    patterns = {
        "district": r"জেলা\s*[:：]?\s*([^\n]+)",
        "upazila": r"উপেজলা/থানা\s*[:：]?\s*([^\n]+)",
        "union_name": r"ইউনিয়ন/ওয়াড(?:র|/ক)?/ক্যাঃ\s*[:：]?\s*([^\n]+)",
        "post_code": r"পোস্টকোড\s*[:：]?\s*([০-৯0-9]+)",
        "ward": r"ওয়াড(?:র)?\s*নম্বর.*?[:：]\s*([০-৯0-9]+)",
        "address": r"ভোটার এলাকার নাম\s*[:：]?\s*([^\n]+)",
    }
    for field, pattern in patterns.items():
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        value = m.group(1).strip()
        result[field] = base.clean(value) if field in {"post_code", "ward"} else base.normalize_field(value, field)
    return result


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
        location = _location(doc)
        progress(min(2, total), total, "reading location pages", 0)

        for page_number in range(3, total + 1):
            progress(page_number, total, "extracting voter records", len(results))
            page = doc[page_number - 1]
            page_records = native_records(page)

            # OCR is now a true fallback. An embedded Bangla font is NOT a
            # reason to OCR a readable text PDF.
            if not page_records:
                fallback = base.ocr_fallback(page)
                if fallback:
                    page_records = fallback
                    any_ocr = True

            for record in page_records:
                for field in ("district", "upazila", "union_name", "ward", "post_code"):
                    if not record.get(field) and location.get(field):
                        record[field] = location[field]
                if not record.get("address") and location.get("address"):
                    record["address"] = location["address"]
                for field in ("district", "upazila", "union_name", "address"):
                    if record.get(field):
                        record[field] = record[field].replace("ময়মনিসংহ", "ময়মনসিংহ").replace("ময়মনিসংহ", "ময়মনসিংহ")
                record["page_number"] = page_number
                record["ocr_used"] = any_ocr
                record["confidence"] = 0.96 if not any_ocr else 0.75
                results.append(record)
            progress(page_number, total, "saving records", len(results))

    progress(total, total, "completed", len(results))
    return results, any_ocr, total
