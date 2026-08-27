from datetime import datetime
from sqlalchemy.orm import Session
from .models import Document, VoterRecord
from .processing import process_pdf
from .pdf_font_decoder import process_pdf as process_native_pdf


def process_document(db: Session, document_id: int):
    doc=db.query(Document).get(document_id)
    if not doc: return
    doc.status="processing"; db.commit()
    try:
        # Prefer the font-aware native decoder. It avoids full-page OCR for
        # readable Bengali text PDFs and falls back to OCR only when needed.
        records,ocr_used,pages=process_native_pdf(doc.stored_path)
        doc.page_count=pages; doc.ocr_used=ocr_used
        for item in records:
            item.pop("ocr_used",None)
            db.add(VoterRecord(document_id=doc.id,pdf_filename=doc.filename,**item))
        doc.status="completed"; doc.error_msg=None; db.commit()
    except Exception as exc:
        doc.status="failed"; doc.error_msg=str(exc)[:2000]; db.commit()
