from sqlalchemy import or_, text
from sqlalchemy.orm import Session
from .models import VoterRecord

DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

SEARCH_COLUMNS = (
    VoterRecord.name, VoterRecord.father_name, VoterRecord.mother_name,
    VoterRecord.voter_id, VoterRecord.district, VoterRecord.upazila,
    VoterRecord.union_name, VoterRecord.ward, VoterRecord.occupation,
    VoterRecord.address, VoterRecord.village, VoterRecord.division,
    VoterRecord.gender, VoterRecord.raw_text,
)


def prepare_fts_query(value: str) -> str:
    tokens = []
    for token in value.translate(DIGITS).strip().split():
        token = token.replace('"', '').replace("'", '')
        if token:
            tokens.append(f'"{token}"*')
    return ' OR '.join(tokens)


def search_records(db: Session, q=None, filters=None, page=1, page_size=50):
    filters = filters or {}
    filters = {
        column: value.translate(DIGITS) if isinstance(value, str) else value
        for column, value in filters.items()
    }
    q = q.translate(DIGITS) if isinstance(q, str) else q
    page = max(1, page)
    page_size = min(max(1, page_size), 200)

    if q and q.strip() and not filters:
        fts_query = prepare_fts_query(q)
        if fts_query:
            try:
                total = db.execute(text(
                    "SELECT COUNT(*) FROM voter_records_fts WHERE voter_records_fts MATCH :q"
                ), {"q": fts_query}).scalar_one()
                ids = [row[0] for row in db.execute(text(
                    "SELECT rowid FROM voter_records_fts WHERE voter_records_fts MATCH :q ORDER BY rowid DESC LIMIT :limit OFFSET :offset"
                ), {"q": fts_query, "limit": page_size, "offset": (page - 1) * page_size}).all()]
                if ids:
                    rows = db.query(VoterRecord).filter(VoterRecord.id.in_(ids)).all()
                    by_id = {row.id: row for row in rows}
                    rows = [by_id[row_id] for row_id in ids if row_id in by_id]
                else:
                    rows = []
                return rows, int(total), True
            except Exception:
                pass

    query = db.query(VoterRecord)
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(*[column.ilike(term) for column in SEARCH_COLUMNS]))
    for column, value in filters.items():
        if value and hasattr(VoterRecord, column):
            query = query.filter(getattr(VoterRecord, column).ilike(f"%{value}%"))

    total = query.count()
    rows = query.order_by(VoterRecord.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return rows, int(total), False
