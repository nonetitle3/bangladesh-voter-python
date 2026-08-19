import io
import pandas as pd
from sqlalchemy.orm import Session
from .models import VoterRecord

COLUMNS = ["voter_id","serial_no","name","father_name","mother_name","birth_date","gender","occupation","address","village","ward","union_name","upazila","district","division","post_code","pdf_filename","page_number","confidence"]

def query_records(db: Session, q=None, district=None, page=1, page_size=5000):
    query = db.query(VoterRecord)
    if q:
        like=f"%{q}%"
        from sqlalchemy import or_
        query=query.filter(or_(VoterRecord.name.ilike(like),VoterRecord.father_name.ilike(like),VoterRecord.mother_name.ilike(like),VoterRecord.voter_id.ilike(like),VoterRecord.district.ilike(like),VoterRecord.address.ilike(like)))
    if district: query=query.filter(VoterRecord.district.ilike(f"%{district}%"))
    total=query.count()
    return query.offset((page-1)*page_size).limit(page_size).all(), total

def dataframe(records):
    rows=[]
    for r in records:
        rows.append({c:(getattr(r,c).isoformat() if hasattr(getattr(r,c),"isoformat") else getattr(r,c)) for c in COLUMNS})
    return pd.DataFrame(rows, columns=COLUMNS)

def csv_bytes(records):
    out=io.StringIO(); dataframe(records).to_csv(out,index=False,encoding="utf-8-sig"); return out.getvalue().encode("utf-8-sig")

def xlsx_bytes(records):
    out=io.BytesIO(); dataframe(records).to_excel(out,index=False,engine="openpyxl"); return out.getvalue()
