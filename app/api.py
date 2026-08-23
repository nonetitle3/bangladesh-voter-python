import os, shutil
from pathlib import Path
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_, text, inspect
from sqlalchemy.orm import Session
from .database import Base, engine, get_db, SessionLocal
from .models import Document, VoterRecord
from .auth import admin_user, authenticate, ensure_admin, make_token
from .processing import process_pdf
from .exporter import query_records, csv_bytes, xlsx_bytes

router=APIRouter(prefix="/api")
UPLOAD_DIR=Path(os.getenv("UPLOAD_DIR","uploads")); UPLOAD_DIR.mkdir(parents=True,exist_ok=True)
Base.metadata.create_all(bind=engine)
try:
    columns={c["name"] for c in inspect(engine).get_columns("documents")}
    wanted={"pdf_data":"BYTEA" if engine.dialect.name=="postgresql" else "BLOB","progress_percent":"INTEGER DEFAULT 0","current_page":"INTEGER DEFAULT 0","total_pages":"INTEGER DEFAULT 0","current_stage":"VARCHAR(100) DEFAULT 'queued'","records_found":"INTEGER DEFAULT 0"}
    with engine.begin() as conn:
        for name,typ in wanted.items():
            if name not in columns: conn.execute(text(f"ALTER TABLE documents ADD COLUMN {name} {typ}"))
except Exception: pass

class Login(BaseModel): username:str; password:str

@router.post("/admin/login")
def login(data:Login,db:Session=Depends(get_db)):
    ensure_admin(db); user=authenticate(db,data.username,data.password)
    if not user: raise HTTPException(401,"Invalid username or password")
    return {"token":make_token(user),"username":user.username,"role":user.role}

@router.get("/admin/config-status")
def config_status():
    return {"ADMIN_USERNAME":bool(os.getenv("ADMIN_USERNAME")),"ADMIN_PASSWORD":bool(os.getenv("ADMIN_PASSWORD")),"ADMIN_PASSWORD_HASH":bool(os.getenv("ADMIN_PASSWORD_HASH")),"JWT_SECRET":bool(os.getenv("JWT_SECRET"))}

def _set_progress(db,doc,page,total,stage,records=0):
    doc.current_page=page; doc.total_pages=total; doc.progress_percent=0 if not total else min(100,round(page*100/total)); doc.current_stage=stage; doc.records_found=records; db.commit()

def _run_document(document_id):
    db=SessionLocal(); doc=None
    try:
        doc=db.get(Document,document_id)
        if not doc:return
        path=Path(doc.stored_path) if doc.stored_path else None
        if not path or not path.exists():
            if doc.pdf_data:
                path=UPLOAD_DIR/f"document_{doc.id}_{Path(doc.filename).name}"; path.write_bytes(doc.pdf_data)
            else: raise RuntimeError("Original PDF is not available")
        doc.status="processing"; doc.progress_percent=0; doc.current_page=0; doc.current_stage="opening PDF"; doc.records_found=0; db.commit()
        def cb(page,total,stage,records): _set_progress(db,doc,page,total,stage,records)
        records,ocr,pages=process_pdf(path,progress_callback=cb)
        _set_progress(db,doc,pages,pages,'saving records',len(records))
        db.query(VoterRecord).filter(VoterRecord.document_id==doc.id).delete(synchronize_session=False)
        for r in records:
            r.pop("ocr_used",None); r.update(document_id=doc.id,pdf_filename=doc.filename); db.add(VoterRecord(**r))
        doc.page_count=pages; doc.ocr_used=ocr; doc.status="completed"; doc.error_msg=None; doc.progress_percent=100; doc.current_page=pages; doc.total_pages=pages; doc.current_stage="completed"; doc.records_found=len(records); db.commit()
    except Exception as e:
        db.rollback(); doc=db.get(Document,document_id)
        if doc:
            doc.status="failed"; doc.error_msg=str(e)[:2000]; doc.current_stage="failed"; db.commit()
    finally: db.close()

@router.post("/admin/upload-pdf")
async def upload_pdfs(background_tasks:BackgroundTasks,files:list[UploadFile]=File(...),user=Depends(admin_user),db:Session=Depends(get_db)):
    created=[]
    for upload in files:
        if not upload.filename.lower().endswith(".pdf"): continue
        safe=Path(upload.filename).name; data=await upload.read(); target=UPLOAD_DIR/safe
        if target.exists(): target=UPLOAD_DIR/f"{Path(safe).stem}_{len(created)+1}.pdf"
        target.write_bytes(data)
        doc=Document(filename=safe,stored_path=str(target),pdf_data=data,status="processing",progress_percent=0,current_page=0,total_pages=0,current_stage="queued",records_found=0); db.add(doc); db.commit(); db.refresh(doc)
        background_tasks.add_task(_run_document,doc.id)
        created.append({"id":doc.id,"filename":safe,"status":"processing","progress_percent":0,"current_page":0,"total_pages":0,"records":0})
    return {"status":"ok","message":"PDF uploaded; OCR started in background","documents":created}

@router.post("/admin/documents/{document_id}/reprocess")
def reprocess_document(document_id:int,background_tasks:BackgroundTasks,user=Depends(admin_user),db:Session=Depends(get_db)):
    doc=db.get(Document,document_id)
    if not doc: raise HTTPException(404,"Document not found")
    doc.status="reprocessing"; doc.progress_percent=0; doc.current_page=0; doc.total_pages=doc.page_count or 0; doc.current_stage="queued"; doc.records_found=0; doc.error_msg=None; db.commit()
    background_tasks.add_task(_run_document,doc.id)
    return {"status":"ok","id":doc.id,"filename":doc.filename,"message":"Re-OCR started","progress_percent":0}

@router.delete("/admin/documents/{document_id}")
def delete_document(document_id:int,user=Depends(admin_user),db:Session=Depends(get_db)):
    doc=db.get(Document,document_id)
    if not doc: raise HTTPException(404,"Document not found")
    path=Path(doc.stored_path) if doc.stored_path else None; db.delete(doc); db.commit()
    if path and path.exists():
        try:path.unlink()
        except OSError:pass
    return {"status":"ok","message":"Document and its voter records deleted","id":document_id}

@router.get("/admin/documents")
def documents(user=Depends(admin_user),db:Session=Depends(get_db)):
    docs=db.query(Document).order_by(Document.uploaded_at.desc()).limit(100).all()
    return [{"id":d.id,"filename":d.filename,"page_count":d.page_count,"status":d.status,"ocr_used":d.ocr_used,"error_msg":d.error_msg,"uploaded_at":d.uploaded_at,"has_pdf":bool(d.pdf_data) or bool(d.stored_path and Path(d.stored_path).exists()),"progress_percent":getattr(d,'progress_percent',0) or 0,"current_page":getattr(d,'current_page',0) or 0,"total_pages":getattr(d,'total_pages',0) or 0,"current_stage":getattr(d,'current_stage','queued') or 'queued',"records_found":getattr(d,'records_found',0) or 0} for d in docs]

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
