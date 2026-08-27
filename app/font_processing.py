import logging
import fitz
from .pdf_font_decoder import native_records, _location
from .ocr_engine import ocr_page

logger = logging.getLogger(__name__)


def _ocr_records(page):
    text, method = ocr_page(page)
    from .processing import native_records as legacy_native_records

    class OCRPage:
        def get_text(self, mode="text"):
            return text

    return legacy_native_records(OCRPage()), method


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
            progress(page_number, total, "extracting native Bengali text", len(results))
            page_records = native_records(page)
            page_used_ocr = False
            method = "native-font"

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
                record["confidence"] = 0.98 if not page_used_ocr else (0.80 if method == "ocr-easyocr" else 0.72)
                results.append(record)

            progress(page_number, total, "records extracted", len(results))

    progress(total, total, "completed", len(results))
    return results, any_ocr, total
