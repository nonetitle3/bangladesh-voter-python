from datetime import datetime
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from .database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True)
    password = Column(String(255), nullable=False)
    role = Column(String(20), default="viewer", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    filename = Column(String(500), nullable=False)
    stored_path = Column(String(1000), nullable=False)
    page_count = Column(Integer, default=0)
    status = Column(String(30), default="pending", nullable=False, index=True)
    ocr_used = Column(Boolean, default=False, nullable=False)
    error_msg = Column(Text)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    records = relationship("VoterRecord", back_populates="document", cascade="all, delete-orphan")

class VoterRecord(Base):
    __tablename__ = "voter_records"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    voter_id = Column(String(100), index=True)
    serial_no = Column(String(100))
    name = Column(String(500), index=True)
    father_name = Column(String(500))
    mother_name = Column(String(500))
    birth_date = Column(Date)
    gender = Column(String(30), index=True)
    occupation = Column(String(300), index=True)
    address = Column(Text)
    village = Column(String(300))
    ward = Column(String(100), index=True)
    union_name = Column(String(300), index=True)
    upazila = Column(String(300), index=True)
    district = Column(String(300), index=True)
    division = Column(String(300), index=True)
    post_code = Column(String(30))
    pdf_filename = Column(String(500))
    page_number = Column(Integer)
    raw_text = Column(Text)
    confidence = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    document = relationship("Document", back_populates="records")
    __table_args__ = (
        Index("ix_voter_name_father", "name", "father_name"),
        Index("ix_voter_district_upazila", "district", "upazila"),
    )
