import json
import os
from pathlib import Path
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session
from .database import Base, engine, get_db, SessionLocal
from .models import Document, VoterRecord
from .auth import admin_user, authenticate, ensure_admin, make_token
from .font_processing import process_pdf
from .exporter import query_records, csv_bytes, xlsx_bytes
from .fts_search import search_records
from .quality import document_quality, merge_confidence

router = APIRouter(prefix="/api")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)


def _ensure_columns():
    try:
        document_columns = {c["name"] for c in inspect(engine).get_columns("documents")}
        voter_columns = {c["name"] for c in inspect(engine).get_columns("voter_records")}
        document_wanted = {
            "pdf_data": "BYTEA" if engine.dialect.name == "postgresql" else "BLOB",
            "progress_percent": "INTEGER DEFAULT 0",
            "current_page": "INTEGER DEFAULT 0",
            "total_pages": "INTEGER DEFAULT 0",
            "current_stage": "VARCHAR(100) DEFAULT 'queued'",
            "records_found": "INTEGER DEFAULT 0",
            "quality_score": "DOUBLE PRECISION" if engine.dialect.name == "postgresql" else "REAL",
            "quality_status": "VARCHAR(20) DEFAULT 'unknown'",
            "quality_review_records": "INTEGER DEFAULT 0",
            "quality_report": "TEXT",
        }
        voter_wanted = {
            "quality_score": "DOUBLE PRECISION" if engine.dialect.name == "postgresql" else "REAL",
            "quality_status": "VARCHAR(20) DEFAULT 'unknown'",
            "quality_issues": "TEXT",
        }
        with engine.begin() as conn:
            for name, typ in document_wanted.items():
                if name not in document_columns:
                    conn.execute(text(f"ALTER TABLE documents ADD COLUMN {name} {typ}"))
            for name, typ in voter_wanted.items():
                if name not in voter_columns:
                    conn.execute(text(f"ALTER TABLE voter_records ADD COLUMN {name} {typ}"))
    except Exception:
        pass


_ensure_columns()


class Login(BaseModel):
    username: str
    password: str


@router.post("/admin/login")
def login(data: Login, db: Session = Depends(get_db)):
    ensure_admin(db)
    user = authenticate(db, data.username, data.password)
    if not user:
        raise HTTPException(401, "Invalid username or password")
    return {"token": make_token(user), "username": user.username, "role": user.role}


@router.get("/admin/config-status")
def config_status():
    return {"ADMIN_USERNAME": bool(os.getenv("ADMIN_USERNAME")), "ADMIN_PASSWORD": bool(os.getenv("ADMIN_PASSWORD")), "ADMIN_PASSWORD_HASH": bool(os.getenv("ADMIN_PASSWORD_HASH")), "JWT_SECRET": bool(os.getenv("JWT_SECRET"))}


def _set_progress(db, doc, page, total, stage, records=0):
    doc.current_page = page
    doc.total_pages = total
    doc.progress_percent = 0 if not total else min(100, round(page * 100 / total))
    doc.current_stage = stage
    doc.records_found = records
    db.commit()


def _run_document(document_id):
    db = SessionLocal()
    try:
        doc = db.get(Document, document_id)
        if not doc:
            return
        path = Path(doc.stored_path) if doc.stored_path else None
        if not path or not path.exists():
            if doc.pdf_data:
                path = UPLOAD_DIR / f"document_{doc.id}_{Path(doc.filename).name}"
                path.write_bytes(doc.pdf_data)
            else:
                raise RuntimeError("Original PDF is not available")
        doc.status = "processing"
        doc.current_stage = "opening PDF"
        doc.progress_percent = 0
        doc.current_page = 0
        doc.records_found = 0
        doc.quality_status = "unknown"
        doc.quality_review_records = 0
        doc.quality_report = None
        db.commit()

        def cb(page, total, stage, records):
            _set_progress(db, doc, page, total, stage, records)

        records, ocr, pages = process_pdf(path, progress_callback=cb)
        _set_progress(db, doc, pages, pages, "validating extraction quality", len(records))
        quality = document_quality(records)

        db.query(VoterRecord).filter(VoterRecord.document_id == doc.id).delete(synchronize_session=False)
        for r in records:
            r.pop("ocr_used", None)
            r.update(document_id=doc.id, pdf_filename=doc.filename)
            qr = merge_confidence(r)
            r["confidence"] = qr.score
            r["quality_score"] = qr.score
            r["quality_status"] = qr.status
            r["quality_issues"] = json.dumps(list(qr.issues), ensure_ascii=False)
            db.add(VoterRecord(**r))

        doc.page_count = pages
        doc.ocr_used = ocr
        doc.status = "completed"
        doc.error_msg = None
        doc.progress_percent = 100
        doc.current_page = pages
        doc.total_pages = pages
        doc.current_stage = "completed"
        doc.records_found = len(records)
        doc.quality_score = quality["average_score"]
        doc.quality_status = "review" if quality["review_records"] else "ok"
        doc.quality_review_records = quality["review_records"]
        doc.quality_report = json.dumps(quality, ensure_ascii=False)
        db.commit()
    except Exception as e:
        db.rollback()
        doc = db.get(Document, document_id)
        if doc:
            doc.status = "failed"
            doc.error_msg = str(e)[:2000]
            doc.current_stage = "failed"
            db.commit()
    finally:
        db.close()


@router.post("/admin/upload-pdf")
async def upload_pdfs(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...), user=Depends(admin_user), db: Session = Depends(get_db)):
    created = []
    for upload in files:
        if not upload.filename or not upload.filename.lower().endswith(".pdf"):
            continue
        safe = Path(upload.filename).name
        data = await upload.read()
        target = UPLOAD_DIR / safe
        if target.exists():
            target = UPLOAD_DIR / f"{Path(safe).stem}_{len(created) + 1}.pdf"
        target.write_bytes(data)
        doc = Document(filename=safe, stored_path=str(target), pdf_data=data, status="processing", progress_percent=0, current_page=0, total_pages=0, current_stage="queued", records_found=0, quality_status="unknown")
        db.add(doc)
        db.commit()
        db.refresh(doc)
        background_tasks.add_task(_run_document, doc.id)
        created.append({"id": doc.id, "filename": safe, "status": "processing", "progress_percent": 0, "current_page": 0, "total_pages": 0, "records": 0})
    if not created:
        raise HTTPException(400, "No valid PDF files uploaded")
    return {"status": "ok", "message": "PDF uploaded; extraction/OCR started in background", "documents": created}


@router.post("/admin/documents/{document_id}/reprocess")
def reprocess_document(document_id: int, background_tasks: BackgroundTasks, user=Depends(admin_user), db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    doc.status = "reprocessing"
    doc.progress_percent = 0
    doc.current_page = 0
    doc.total_pages = doc.page_count or 0
    doc.current_stage = "queued"
    doc.records_found = 0
    doc.error_msg = None
    doc.quality_status = "unknown"
    doc.quality_review_records = 0
    doc.quality_report = None
    db.commit()
    background_tasks.add_task(_run_document, doc.id)
    return {"status": "ok", "id": doc.id, "filename": doc.filename, "message": "Reprocessing started", "progress_percent": 0}


@router.delete("/admin/documents/{document_id}")
def delete_document(document_id: int, user=Depends(admin_user), db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    path = Path(doc.stored_path) if doc.stored_path else None
    db.delete(doc)
    db.commit()
    if path and path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    return {"status": "ok", "message": "Document and its voter records deleted", "id": document_id}


@router.get("/admin/documents")
def documents(user=Depends(admin_user), db: Session = Depends(get_db)):
    docs = db.query(Document).order_by(Document.uploaded_at.desc()).limit(100).all()
    return [{"id": d.id, "filename": d.filename, "page_count": d.page_count, "status": d.status, "ocr_used": d.ocr_used, "error_msg": d.error_msg, "uploaded_at": d.uploaded_at, "has_pdf": bool(d.pdf_data) or bool(d.stored_path and Path(d.stored_path).exists()), "progress_percent": getattr(d, "progress_percent", 0) or 0, "current_page": getattr(d, "current_page", 0) or 0, "total_pages": getattr(d, "total_pages", 0) or 0, "current_stage": getattr(d, "current_stage", "queued") or "queued", "records_found": getattr(d, "records_found", 0) or 0, "quality_score": getattr(d, "quality_score", None), "quality_status": getattr(d, "quality_status", "unknown") or "unknown", "quality_review_records": getattr(d, "quality_review_records", 0) or 0} for d in docs]


@router.get("/admin/documents/{document_id}/quality")
def document_quality_endpoint(document_id: int, user=Depends(admin_user), db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    report = {}
    if doc.quality_report:
        try:
            report = json.loads(doc.quality_report)
        except json.JSONDecodeError:
            report = {}
    return {"id": doc.id, "filename": doc.filename, "status": doc.status, "quality_score": doc.quality_score, "quality_status": doc.quality_status, "quality_review_records": doc.quality_review_records, "report": report}


@router.get("/admin/review-records")
def review_records(document_id: int | None = None, limit: int = 100, offset: int = 0, user=Depends(admin_user), db: Session = Depends(get_db)):
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    query = db.query(VoterRecord).filter(VoterRecord.quality_status == "review")
    if document_id is not None:
        query = query.filter(VoterRecord.document_id == document_id)
    total = query.count()
    rows = query.order_by(VoterRecord.id.asc()).offset(offset).limit(limit).all()
    cols = [c.name for c in VoterRecord.__table__.columns]
    return {"records": [{c: getattr(row, c) for c in cols} for row in rows], "total_count": total, "limit": limit, "offset": offset}


@router.get("/voter-search/search")
def search(q: str | None = None, name: str | None = None, father_name: str | None = None, mother_name: str | None = None, voter_id: str | None = None, district: str | None = None, upazila: str | None = None, union_name: str | None = None, ward: str | None = None, occupation: str | None = None, gender: str | None = None, page: int = 1, page_size: int = 50, db: Session = Depends(get_db), user=Depends(admin_user)):
    filters = {k: v for k, v in {"name": name, "father_name": father_name, "mother_name": mother_name, "voter_id": voter_id, "district": district, "upazila": upazila, "union_name": union_name, "ward": ward, "occupation": occupation, "gender": gender}.items() if v}
    rows, total, fts_used = search_records(db, q, filters, page, page_size)
    cols = [c.name for c in VoterRecord.__table__.columns]
    return {"records": [{c: getattr(r, c) for c in cols} for r in rows], "total_count": total, "page": max(1, page), "page_size": min(max(1, page_size), 200), "search_engine": "fts5" if fts_used else "like"}


@router.get("/voter-search/stats")
def stats(db: Session = Depends(get_db), user=Depends(admin_user)):
    def top(col):
        return [{"value": v, "count": n} for v, n in db.query(col, func.count(VoterRecord.id)).filter(col.isnot(None), col != "").group_by(col).order_by(func.count(VoterRecord.id).desc()).limit(8).all()]
    return {"total_pdfs": db.query(Document).count(), "total_records": db.query(VoterRecord).count(), "review_records": db.query(VoterRecord).filter(VoterRecord.quality_status == "review").count(), "top_districts": [{"district": x["value"], "count": x["count"]} for x in top(VoterRecord.district)], "top_occupations": [{"occupation": x["value"], "count": x["count"]} for x in top(VoterRecord.occupation)], "gender_breakdown": [{"gender": x["value"], "count": x["count"]} for x in top(VoterRecord.gender)]}


@router.get("/voter-search/export/csv")
def export_csv(q: str | None = None, district: str | None = None, db: Session = Depends(get_db), user=Depends(admin_user)):
    records, _ = query_records(db, q, district, page_size=100000)
    return Response(csv_bytes(records), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=voter-results.csv"})


@router.get("/voter-search/export/xlsx")
def export_xlsx(q: str | None = None, district: str | None = None, db: Session = Depends(get_db), user=Depends(admin_user)):
    records, _ = query_records(db, q, district, page_size=100000)
    return Response(xlsx_bytes(records), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=voter-results.xlsx"})
