import os
import logging
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./voter.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
Base = declarative_base()

if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_database():
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    if not IS_SQLITE:
        return

    with engine.begin() as conn:
        try:
            conn.execute(text("""
                CREATE VIRTUAL TABLE IF NOT EXISTS voter_records_fts
                USING fts5(
                    name, father_name, mother_name, voter_id, address,
                    village, ward, union_name, upazila, district, division,
                    occupation, gender, raw_text,
                    content='voter_records', content_rowid='id',
                    tokenize='unicode61 remove_diacritics 0'
                )
            """))
            conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS voter_records_ai
                AFTER INSERT ON voter_records
                BEGIN
                    INSERT INTO voter_records_fts(
                        rowid, name, father_name, mother_name, voter_id,
                        address, village, ward, union_name, upazila,
                        district, division, occupation, gender, raw_text
                    ) VALUES (
                        new.id, new.name, new.father_name, new.mother_name,
                        new.voter_id, new.address, new.village, new.ward,
                        new.union_name, new.upazila, new.district, new.division,
                        new.occupation, new.gender, new.raw_text
                    );
                END
            """))
            conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS voter_records_ad
                AFTER DELETE ON voter_records
                BEGIN
                    INSERT INTO voter_records_fts(
                        voter_records_fts, rowid, name, father_name, mother_name,
                        voter_id, address, village, ward, union_name, upazila,
                        district, division, occupation, gender, raw_text
                    ) VALUES (
                        'delete', old.id, old.name, old.father_name, old.mother_name,
                        old.voter_id, old.address, old.village, old.ward,
                        old.union_name, old.upazila, old.district, old.division,
                        old.occupation, old.gender, old.raw_text
                    );
                END
            """))
            conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS voter_records_au
                AFTER UPDATE ON voter_records
                BEGIN
                    INSERT INTO voter_records_fts(
                        voter_records_fts, rowid, name, father_name, mother_name,
                        voter_id, address, village, ward, union_name, upazila,
                        district, division, occupation, gender, raw_text
                    ) VALUES (
                        'delete', old.id, old.name, old.father_name, old.mother_name,
                        old.voter_id, old.address, old.village, old.ward,
                        old.union_name, old.upazila, old.district, old.division,
                        old.occupation, old.gender, old.raw_text
                    );
                    INSERT INTO voter_records_fts(
                        rowid, name, father_name, mother_name, voter_id,
                        address, village, ward, union_name, upazila,
                        district, division, occupation, gender, raw_text
                    ) VALUES (
                        new.id, new.name, new.father_name, new.mother_name,
                        new.voter_id, new.address, new.village, new.ward,
                        new.union_name, new.upazila, new.district, new.division,
                        new.occupation, new.gender, new.raw_text
                    );
                END
            """))
            conn.execute(text("INSERT INTO voter_records_fts(voter_records_fts) VALUES('rebuild')"))
            logger.info("SQLite FTS5 index initialized")
        except Exception:
            logger.exception("SQLite FTS5 initialization failed; LIKE search remains available")
