import logging
import fitz
import pdfplumber
from .pdf_font_decoder import native_records, _location
from .ocr_engine import ocr_page
from . import processing as base

logger = logging.getLogger(__name__)


def _records_from_text(text):
    """Parse text produced by pdfplumber/OCR using the existing parser."""
    class TextPage:
        def get_text(self, mode="text"):
            return text or ""

    return base.native_records(TextPage())


def process_pdf(file_path, progress_callback=None):
    """Extract records with native-font, pdfplumber, then OCR fallback."""
    results = []
    any_ocr = False
    total = 0

    def progress(page, total_pages, stage, records):
        if progress_callback:
            try:
                progress_callback(page, total_pages, stage, records)
            except Exception:
                logger.exception("Progress callback failed")

    plumber_doc = None
    try:
        with fitz.open(file_path) as doc:
            total = len(doc)
            location = _location(doc)
            progress(0, total, "opening PDF", 0)

            for page_number, page in enumerate(doc, start=1):
                progress(page_number, total, "extracting native Bengali text", len(results))
                page_records = native_records(page)
                page_used_ocr = False
                method = "native-font"

                if not page_records:
                    try:
                        if plumber_doc is None:
                            plumber_doc = pdfplumber.open(str(file_path))
                        plumber_text = plumber_doc.pages[page_number - 1].extract_text(
                            x_tolerance=2,
                            y_tolerance=3,
                        ) or ""
                        if plumber_text.strip():
                            page_records = _records_from_text(plumber_text)
                            method = "pdfplumber"
                    except Exception:
                        logger.exception("pdfplumber fallback failed on page %s", page_number)

                if not page_records:
                    try:
                        ocr_text, ocr_method = ocr_page(page)
                        if ocr_text.strip():
                            page_records = _records_from_text(ocr_text)
                            method = ocr_method
                            page_used_ocr = bool(page_records)
                            any_ocr = any_ocr or page_used_ocr
                    except Exception:
                        logger.exception("OCR fallback failed on page %s", page_number)

                for record in page_records:
                    for field in ("district", "upazila", "union_name", "ward", "post_code"):
                        if not record.get(field) and location.get(field):
                            record[field] = location[field]
                    if not record.get("address") and location.get("address"):
                        record["address"] = location["address"]
                    record["page_number"] = page_number
                    record["ocr_used"] = page_used_ocr
                    record["extraction_method"] = method
                    record["confidence"] = {
                        "native-font": 0.98,
                        "pdfplumber": 0.94,
                        "ocr-easyocr": 0.80,
                        "ocr-tesseract": 0.72,
                    }.get(method, 0.70)
                    results.append(record)

                progress(page_number, total, "records extracted", len(results))
    finally:
        if plumber_doc is not None:
            plumber_doc.close()

    progress(total, total, "completed", len(results))
    return results, any_ocr, total
