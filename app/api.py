import os
import shutil
import tempfile
from pathlib import Path
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from .database import Base, engine, get_db
from .models import Document, VoterRecord
from .auth import admin_user, authenticate, ensure_admin, make_token
from .processing import process_pdf

router=APIRouter(prefix="/api")
UPLOAD_DIR=Path(os.getenv("UPLOAD_DIR","uploads")); UPLOAD_DIR.mkdir(parents=True,exist_ok=True)
Base.metadata.create_all(bind=engine)

class Login(BaseModel):
    username:str
    password:str

@router.post("/admin/login")
def login(data:Login,db:Session=Depends(get_db)):
    ensure_admin(db); user=authenticate(db,data.username,data.password)
    if not user: raise HTTPException(status_code=401,detail="Invalid username or password")
    return {"token":make_token(user),"username":user.username,"role":user.role}

@router.get("/admin/config-status")
def config_status(db:Session=Depends(get_db)):
    ensure_admin(db)
    return {"ADMIN_USERNAME":bool(os.getenv("ADMIN_USERNAME")),"ADMIN_PASSWORD":bool(os.getenv("ADMIN_PASSWORD")),"ADMIN_PASSWORD_HASH":bool(os.getenv("ADMIN_PASSWORD_HASH")),"JWT_SECRET":bool(os.getenv("JWT_SECRET"))}

@router.post("/admin/upload-pdf")
async def upload_pdfs(files:list[UploadFile]=File(...),user=Depends(admin_user),db:Session=Depends(get_db)):
    created=[]
    for upload in files:
        if not upload.filename.lower().endswith(".pdf"): continue
        safe=Path(upload.filename).name; target=UPLOAD_DIR/safe
        if target.exists(): target=UPLOAD_DIR/f"{Path(safe).stem}_{len(created)+1}.pdf"
        with target.open("wb") as out: shutil.copyfileobj(upload.file,out)
        doc=Document(filename=safe,stored_path=str(target),status="processing");db.add(doc);db.commit();db.refresh(doc)
        try:
            records,ocr,pages=process_pdf(target);doc.page_count=pages;doc.ocr_used=ocr
            for r in records:
                r.pop("ocr_used",None);r["document_id"]=doc.id;r["pdf_filename"]=safe
                db.add(VoterRecord(**r))
            doc.status="completed";db.commit();created.append({"id":doc.id,"filename":safe,"records":len(records),"ocr_used":ocr})
        except Exception as e:
            db.rollback(); doc=db.get(Document,doc.id);doc.status="failed";doc.error_msg=str(e);db.commit();created.append({"id":doc.id,"filename":safe,"error":str(e)})
    return {"status":"ok","message":"PDF processing completed","documents":created}

@router.get("/voter-search/search")
def search(q:str|None=None,name:str|None=None,father_name:str|None=None,mother_name:str|None=None,voter_id:str|None=None,district:str|None=None,upazila:str|None=None,union_name:str|None=None,ward:str|None=None,occupation:str|None=None,gender:str|None=None,page:int=1,page_size:int=50,db:Session=Depends(get_db),user=Depends(admin_user)):
    query=db.query(VoterRecord)
    if q:
        from sqlalchemy import or_; term=f"%{q}%"; query=query.filter(or_(VoterRecord.name.ilike(term),VoterRecord.father_name.ilike(term),VoterRecord.mother_name.ilike(term),VoterRecord.voter_id.ilike(term),VoterRecord.district.ilike(term),VoterRecord.address.ilike(term)))
    for col,val in [(VoterRecord.name,name),(VoterRecord.father_name,father_name),(VoterRecord.mother_name,mother_name),(VoterRecord.voter_id,voter_id),(VoterRecord.district,district),(VoterRecord.upazila,upazila),(VoterRecord.union_name,union_name),(VoterRecord.ward,ward),(VoterRecord.occupation,occupation)]:
        if val: query=query.filter(col.ilike(f"%{val}%"))
    if gender: query=query.filter(VoterRecord.gender==gender)
    total=query.count(); rows=query.offset(max(0,page-1)*page_size).limit(page_size).all()
    return {"records":[{c.name:getattr(r,c.name) for c in VoterRecord.__table__.columns} for r in rows],"total_count":total,"page":page,"page_size":page_size}

@router.get("/voter-search/stats")
def stats(db:Session=Depends(get_db),user=Depends(admin_user)):
    from sqlalchemy import func
    def top(col): return [{"value":v,"count":n} for v,n in db.query(col,func.count(VoterRecord.id)).filter(col.isnot(None),col!='').group_by(col).order_by(func.count(VoterRecord.id).desc()).limit(8).all()]
    docs=db.query(Document).count(); records=db.query(VoterRecord).count()
    return {"total_pdfs":docs,"total_records":records,"top_districts":[{"district":x["value"],"count":x["count"]} for x in top(VoterRecord.district)],"top_occupations":[{"occupation":x["value"],"count":x["count"]} for x in top(VoterRecord.occupation)],"gender_breakdown":[{"gender":x["value"],"count":x["count"]} for x in top(VoterRecord.gender)]}
