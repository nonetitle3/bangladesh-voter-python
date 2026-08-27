import logging
import fitz
import pdfplumber
from .pdf_font_decoder import native_records, _location
from .ocr_engine import ocr_page

logger = logging.getLogger(__name__)


def _records_from_text(text):
    from .processing import native_records as legacy_native_records

    class TextPage:
        def get_text(self, mode="text"):
            return text or ""

    return legacy_native_records(TextPage())


def _ocr_records(page):
    text, method = ocr_page(page)
    return _records_from_text(text), method


def process_pdf(file_path, progress_callback=None):
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
                        plumber_text = plumber_doc.pages[page_number - 1].extract_text(x_tolerance=2, y_tolerance=3) or ""
                        page_records = _records_from_text(plumber_text)
                        method = "pdfplumber"
                    except Exception:
                        logger.exception("pdfplumber fallback failed on page %s", page_number)
                        page_records = []

                if not page_records:
                    try:
                        page_records, method = _ocr_records(page)
                    except Exception:
                        logger.exception("OCR failed on page %s", page_number)
                        page_records = []
                    if page_records:
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
                    record["extraction_method"] = method
                    record["confidence"] = 0.98 if method == "native-font" else (0.94 if method == "pdfplumber" else (0.80 if method == "ocr-easyocr" else 0.72))
                    results.append(record)

                progress(page_number, total, "records extracted", len(results))
    finally:
        if plumber_doc is not None:
            plumber_doc.close()

    progress(total, total, "completed", len(results))
    return results, any_ocr, total
