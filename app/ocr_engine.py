import logging
import threading
import fitz
from PIL import Image, ImageEnhance, ImageOps
import pytesseract

logger = logging.getLogger(__name__)
_reader = None
_reader_lock = threading.Lock()


def _easyocr_reader():
    global _reader
    if _reader is None:
        with _reader_lock:
            if _reader is None:
                import easyocr
                _reader = easyocr.Reader(["bn", "en"], gpu=False, verbose=False)
    return _reader


def easyocr_page(page):
    pix = page.get_pixmap(dpi=200, alpha=False)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    result = _easyocr_reader().readtext(image, detail=0, paragraph=True)
    text = "\n".join(str(item).strip() for item in result if str(item).strip())
    if not text.strip():
        raise RuntimeError("EasyOCR returned no text")
    return text


def tesseract_page(page):
    pix = page.get_pixmap(dpi=180, alpha=False)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    image = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.25)
    try:
        languages = set(pytesseract.get_languages(config=""))
    except Exception:
        languages = set()
    lang = "ben+eng" if {"ben", "eng"}.issubset(languages) else "eng"
    outputs = []
    for psm in (6, 11):
        text = pytesseract.image_to_string(image, lang=lang, config=f"--psm {psm}")
        if text.strip() and text not in outputs:
            outputs.append(text)
    return "\n".join(outputs)


def ocr_page(page):
    try:
        return easyocr_page(page), "ocr-easyocr"
    except Exception:
        logger.exception("EasyOCR failed; falling back to Tesseract")
    return tesseract_page(page), "ocr-tesseract"
