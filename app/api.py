import os, shutil
from pathlib import Path
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_, text, inspect
from sqlalchemy.orm import Session
from .database import Base, engine, get_db
from .models import Document, VoterRecord
from .auth import admin_user, authenticate, ensure_admin, make_token
from .processing import process_pdf
from .exporter import query_records, csv_bytes, xlsx_bytes

router=APIRouter(prefix="/api")
UPLOAD_DIR=Path(os.getenv("UPLOAD_DIR","uploads")); UPLOAD_DIR.mkdir(parents=True,exist_ok=True)
Base.metadata.create_all(bind=engine)

# Backward-compatible schema upgrade for databases created before pdf_data existed.
try:
    columns={c["name"] for c in inspect(engine).get_columns("documents")}
    if "pdf_data" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE documents ADD COLUMN pdf_data BYTEA" if engine.dialect.name == "postgresql" else "ALTER TABLE documents ADD COLUMN pdf_data BLOB"))
except Exception:
    pass

class Login(BaseModel): username:str; password:str

@router.post("/admin/login")
def login(data:Login,db:Session=Depends(get_db)):
    ensure_admin(db); user=authenticate(db,data.username,data.password)
    if not user: raise HTTPException(401,"Invalid username or password")
    return {"token":make_token(user),"username":user.username,"role":user.role}

@router.get("/admin/config-status")
def config_status():
    return {"ADMIN_USERNAME":bool(os.getenv("ADMIN_USERNAME")),"ADMIN_PASSWORD":bool(os.getenv("ADMIN_PASSWORD")),"ADMIN_PASSWORD_HASH":bool(os.getenv("ADMIN_PASSWORD_HASH")),"JWT_SECRET":bool(os.getenv("JWT_SECRET"))}

@router.post("/admin/upload-pdf")
async def upload_pdfs(files:list[UploadFile]=File(...),user=Depends(admin_user),db:Session=Depends(get_db)):
    created=[]
    for upload in files:
        if not upload.filename.lower().endswith(".pdf"): continue
        safe=Path(upload.filename).name; data=await upload.read(); target=UPLOAD_DIR/safe
        if target.exists(): target=UPLOAD_DIR/f"{Path(safe).stem}_{len(created)+1}.pdf"
        target.write_bytes(data)
        doc=Document(filename=safe,stored_path=str(target),pdf_data=data,status="processing"); db.add(doc); db.commit(); db.refresh(doc)
        try:
            records,ocr,pages=process_pdf(target); doc.page_count=pages; doc.ocr_used=ocr; doc.error_msg=None
            for r in records:
                r.pop("ocr_used",None); r.update(document_id=doc.id,pdf_filename=safe); db.add(VoterRecord(**r))
            doc.status="completed"; db.commit(); created.append({"id":doc.id,"filename":safe,"records":len(records),"ocr_used":ocr,"status":"completed"})
        except Exception as e:
            db.rollback(); doc=db.get(Document,doc.id); doc.status="failed"; doc.error_msg=str(e)[:2000]; db.commit(); created.append({"id":doc.id,"filename":safe,"status":"failed","error":doc.error_msg})
    return {"status":"ok","message":"PDF processing completed","documents":created}

@router.post("/admin/documents/{document_id}/reprocess")
def reprocess_document(document_id:int,user=Depends(admin_user),db:Session=Depends(get_db)):
    doc=db.get(Document,document_id)
    if not doc: raise HTTPException(404,"Document not found")
    path=Path(doc.stored_path) if doc.stored_path else None
    if not path or not path.exists():
        if doc.pdf_data:
            path=UPLOAD_DIR/f"reprocess_{doc.id}_{Path(doc.filename).name}"; path.write_bytes(doc.pdf_data)
        else:
            raise HTTPException(409,"Original PDF is not available for re-OCR")
    old_status=doc.status
    try:
        doc.status="reprocessing"; doc.error_msg=None; db.commit()
        records,ocr,pages=process_pdf(path)
        db.query(VoterRecord).filter(VoterRecord.document_id==doc.id).delete(synchronize_session=False)
        for r in records:
            r.pop("ocr_used",None); r.update(document_id=doc.id,pdf_filename=doc.filename); db.add(VoterRecord(**r))
        doc.page_count=pages; doc.ocr_used=ocr; doc.status="completed"; doc.error_msg=None; db.commit()
        return {"status":"ok","id":doc.id,"filename":doc.filename,"records":len(records),"pages":pages,"ocr_used":ocr}
    except Exception as e:
        db.rollback(); doc=db.get(Document,document_id); doc.status=old_status or "failed"; doc.error_msg=str(e)[:2000]; db.commit()
        raise HTTPException(500,f"Re-OCR failed: {doc.error_msg}")

@router.get("/admin/documents")
def documents(user=Depends(admin_user),db:Session=Depends(get_db)):
    docs=db.query(Document).order_by(Document.uploaded_at.desc()).limit(100).all()
    return [{"id":d.id,"filename":d.filename,"page_count":d.page_count,"status":d.status,"ocr_used":d.ocr_used,"error_msg":d.error_msg,"uploaded_at":d.uploaded_at,"has_pdf":bool(d.pdf_data) or bool(d.stored_path and Path(d.stored_path).exists())} for d in docs]

@router.get("/voter-search/search")
def search(q:str|None=None,name:str|None=None,father_name:str|None=None,mother_name:str|None=None,voter_id:str|None=None,district:str|None=None,upazila:str|None=None,union_name:str|None=None,ward:str|None=None,occupation:str|None=None,gender:str|None=None,page:int=1,page_size:int=50,db:Session=Depends(get_db),user=Depends(admin_user)):
    query=db.query(VoterRecord)
    if q:
        term=f"%{q}%"; query=query.filter(or_(VoterRecord.name.ilike(term),VoterRecord.father_name.ilike(term),VoterRecord.mother_name.ilike(term),VoterRecord.voter_id.ilike(term),VoterRecord.district.ilike(term),VoterRecord.address.ilike(term),VoterRecord.raw_text.ilike(term)))
    for col,val in [(VoterRecord.name,name),(VoterRecord.father_name,father_name),(VoterRecord.mother_name,mother_name),(VoterRecord.voter_id,voter_id),(VoterRecord.district,district),(VoterRecord.upazila,upazila),(VoterRecord.union_name,union_name),(VoterRecord.ward,ward),(VoterRecord.occupation,occupation)]:
        if val:
            term=f"%{val}%"; query=query.filter(or_(col.ilike(term),VoterRecord.raw_text.ilike(term)))
    if gender:
        term=f"%{gender}%"; query=query.filter(or_(VoterRecord.gender.ilike(term),VoterRecord.raw_text.ilike(term)))
    page=max(1,page); page_size=min(max(1,page_size),200); total=query.count(); rows=query.order_by(VoterRecord.id.desc()).offset((page-1)*page_size).limit(page_size).all(); cols=[c.name for c in VoterRecord.__table__.columns]
    return {"records":[{c:getattr(r,c) for c in cols} for r in rows],"total_count":total,"page":page,"page_size":page_size}

@router.get("/voter-search/stats")
def stats(db:Session=Depends(get_db),user=Depends(admin_user)):
    def top(col): return [{"value":v,"count":n} for v,n in db.query(col,func.count(VoterRecord.id)).filter(col.isnot(None),col!='').group_by(col).order_by(func.count(VoterRecord.id).desc()).limit(8).all()]
    return {"total_pdfs":db.query(Document).count(),"total_records":db.query(VoterRecord).count(),"top_districts":[{"district":x["value"],"count":x["count"]} for x in top(VoterRecord.district)],"top_occupations":[{"occupation":x["value"],"count":x["count"]} for x in top(VoterRecord.occupation)],"gender_breakdown":[{"gender":x["value"],"count":x["count"]} for x in top(VoterRecord.gender)]}

@router.get("/voter-search/export/csv")
def export_csv(q:str|None=None,district:str|None=None,db:Session=Depends(get_db),user=Depends(admin_user)):
    records,_=query_records(db,q,district,page_size=100000); return Response(csv_bytes(records),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":"attachment; filename=voter-results.csv"})

@router.get("/voter-search/export/xlsx")
def export_xlsx(q:str|None=None,district:str|None=None,db:Session=Depends(get_db),user=Depends(admin_user)):
    records,_=query_records(db,q,district,page_size=100000); return Response(xlsx_bytes(records),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":"attachment; filename=voter-results.xlsx"})
