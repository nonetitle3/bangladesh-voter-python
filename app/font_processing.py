import logging
from pathlib import Path
import fitz
from .pdf_font_decoder import decoded_text, native_records, _location
from .processing import ocr_fallback

logger = logging.getLogger(__name__)


def process_pdf(file_path, progress_callback=None):
    results = []
    any_ocr = False

    def progress(page, total, stage, records):
        if progress_callback:
            try:
                progress_callback(page, total, stage, records)
            except Exception:
                logger.exception("Progress callback failed")

    with fitz.open(file_path) as doc:
        total = len(doc)
        location = _location(doc)
        progress(0, total, "opening PDF", 0)

        for page_number, page in enumerate(doc, start=1):
            progress(page_number, total, "extracting native PDF text", len(results))
            page_records = native_records(page)
            page_used_ocr = False

            if not page_records:
                try:
                    ocr_records = ocr_fallback(page)
                except Exception:
                    logger.exception("OCR fallback failed on page %s", page_number)
                    ocr_records = []
                if ocr_records:
                    page_records = ocr_records
                    page_used_ocr = True
                    any_ocr = True

            for record in page_records:
                for field in ("district", "upazila", "union_name", "ward", "post_code"):
                    if not record.get(field) and location.get(field):
                        record[field] = location[field]
                if not record.get("address") and location.get("address"):
                    record["address"] = location["address"]
                record["page_number"] = page_number
                record["ocr_used"] = page_used_ocr
                record["confidence"] = 0.98 if not page_used_ocr else 0.75
                results.append(record)

            progress(page_number, total, "records extracted", len(results))

    progress(total, total, "completed", len(results))
    return results, any_ocr, total
