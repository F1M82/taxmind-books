from sqlalchemy import Column, String, Integer, Float, DateTime, Boolean, ForeignKey, Text, DECIMAL, Date, JSON, Enum
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime
from decimal import Decimal as PyDecimal
import uuid
import enum

class CompanyStatus(enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"

class VoucherStatus(enum.Enum):
    DRAFT = "draft"
    POSTED = "posted"
    CANCELLED = "cancelled"
    PENDING_APPROVAL = "pending_approval"

class ReconciliationStatus(enum.Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"

class Company(Base):
    __tablename__ = "companies"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    gstin = Column(String(15), unique=True)
    pan = Column(String(10))
    financial_year_start = Column(Date, default=datetime(2024, 4, 1).date())
    accounting_source = Column(String(50), default="standalone")  # tally, zoho, quickbooks, standalone
    status = Column(Enum(CompanyStatus), default=CompanyStatus.ACTIVE)
    created_by = Column(String, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    ledgers = relationship("Ledger", back_populates="company", cascade="all, delete-orphan")
    vouchers = relationship("Voucher", back_populates="company", cascade="all, delete-orphan")
    recon_sessions = relationship("ReconciliationSession", back_populates="company", cascade="all, delete-orphan")
    users = relationship("UserCompany", back_populates="company")

class Ledger(Base):
    __tablename__ = "ledgers"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, ForeignKey("companies.id"), nullable=False)
    name = Column(String(255), nullable=False)
    group_name = Column(String(100))  # Sundry Debtors, Creditors, Bank, etc.
    opening_balance = Column(DECIMAL(15, 2), default=0)
    balance_type = Column(String(2), default="Dr")  # Dr, Cr
    gstin = Column(String(15))
    pan = Column(String(10))
    phone = Column(String(15))
    email = Column(String(255))
    address = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    company = relationship("Company", back_populates="ledgers")
    entries = relationship("LedgerEntry", back_populates="ledger", cascade="all, delete-orphan")

class Voucher(Base):
    __tablename__ = "vouchers"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, ForeignKey("companies.id"), nullable=False)
    voucher_type = Column(String(50), nullable=False)  # Receipt, Payment, Sales, Purchase, Journal, Contra
    voucher_number = Column(String(50))
    date = Column(Date, nullable=False)
    narration = Column(Text)
    reference = Column(String(100))  # Invoice no, UTR, Cheque no
    total_amount = Column(DECIMAL(15, 2), nullable=False)
    created_by = Column(String, ForeignKey("users.id"))
    approved_by = Column(String, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = Column(Enum(VoucherStatus), default=VoucherStatus.POSTED)
    source = Column(String(20))  # voice, photo, manual, sms, email, import
    is_auto_posted = Column(Boolean, default=False)
    confidence_score = Column(Float)
    
    # GST & TDS
    gst_applicable = Column(Boolean, default=False)
    gst_rate = Column(DECIMAL(5, 2))
    cgst = Column(DECIMAL(15, 2), default=0)
    sgst = Column(DECIMAL(15, 2), default=0)
    igst = Column(DECIMAL(15, 2), default=0)
    tds_applicable = Column(Boolean, default=False)
    tds_amount = Column(DECIMAL(15, 2), default=0)
    tds_section = Column(String(10))
    
    # Relationships
    company = relationship("Company", back_populates="vouchers")
    entries = relationship("LedgerEntry", back_populates="voucher", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="voucher")

class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    voucher_id = Column(String, ForeignKey("vouchers.id"), nullable=False)
    ledger_id = Column(String, ForeignKey("ledgers.id"), nullable=False)
    amount = Column(DECIMAL(15, 2), nullable=False)
    entry_type = Column(String(2), nullable=False)  # Dr, Cr
    gst_rate = Column(DECIMAL(5, 2))
    cgst = Column(DECIMAL(15, 2))
    sgst = Column(DECIMAL(15, 2))
    igst = Column(DECIMAL(15, 2))
    tds_amount = Column(DECIMAL(15, 2))
    tds_section = Column(String(10))
    
    # Relationships
    voucher = relationship("Voucher", back_populates="entries")
    ledger = relationship("Ledger", back_populates="entries")

class ReconciliationSession(Base):
    __tablename__ = "recon_sessions"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, ForeignKey("companies.id"), nullable=False)
    party_id = Column(String)  # Ledger ID or party name
    party_name = Column(String(255))
    period_from = Column(Date, nullable=False)
    period_to = Column(Date, nullable=False)
    status = Column(Enum(ReconciliationStatus), default=ReconciliationStatus.PROCESSING)
    your_balance = Column(DECIMAL(15, 2))
    party_balance = Column(DECIMAL(15, 2))
    difference = Column(DECIMAL(15, 2))
    matched_count = Column(Integer, default=0)
    fuzzy_count = Column(Integer, default=0)
    unmatched_count = Column(Integer, default=0)
    created_by = Column(String, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    
    # Relationships
    company = relationship("Company", back_populates="recon_sessions")
    matches = relationship("ReconciliationMatch", back_populates="recon_session", cascade="all, delete-orphan")

class ReconciliationMatch(Base):
    __tablename__ = "recon_matches"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    recon_session_id = Column(String, ForeignKey("recon_sessions.id"), nullable=False)
    your_voucher_id = Column(String)
    party_voucher_id = Column(String)
    your_transaction_data = Column(JSON)  # Store full transaction data
    party_transaction_data = Column(JSON)
    match_type = Column(String(20))  # exact, fuzzy, amount_only, partial_payment
    match_tier = Column(String(50))  # gstin_exact, reference_exact, amount_date_fuzzy, etc.
    confidence_score = Column(Float)
    status = Column(String(20), default="pending")  # auto_matched, user_confirmed, disputed, rejected
    difference = Column(DECIMAL(15, 2), default=0)
    flags = Column(JSON)  # ["tds_detected", "timing_difference", "duplicate_suspected"]
    suggested_action = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    recon_session = relationship("ReconciliationSession", back_populates="matches")

class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, ForeignKey("companies.id"))
    user_id = Column(String, ForeignKey("users.id"))
    voucher_id = Column(String, ForeignKey("vouchers.id"))
    action = Column(String(50), nullable=False)  # created, updated, deleted, posted, approved
    entity_type = Column(String(50))
    entity_id = Column(String)
    old_value = Column(JSON)
    new_value = Column(JSON)
    ip_address = Column(String(50))
    user_agent = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Relationships
    company = relationship("Company")
    user = relationship("User")
    voucher = relationship("Voucher", back_populates="audit_logs")

class User(Base):
    __tablename__ = "users"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    phone = Column(String(15))
    is_ca = Column(Boolean, default=False)
    firm_name = Column(String(255))
    ca_membership_no = Column(String(50))
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime)
    
    # Relationships
    companies = relationship("UserCompany", back_populates="user")
    created_vouchers = relationship("Voucher", foreign_keys="Voucher.created_by")
    approved_vouchers = relationship("Voucher", foreign_keys="Voucher.approved_by")

class UserCompany(Base):
    """Many-to-many relationship between users and companies"""
    __tablename__ = "user_companies"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False)
    role = Column(String(20), default="viewer")  # owner, admin, editor, viewer
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="companies")
    company = relationship("Company", back_populates="users")

class PaymentDetection(Base):
    """Store detected payments from SMS/Email"""
    __tablename__ = "payment_detections"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, ForeignKey("companies.id"), nullable=False)
    source = Column(String(20), nullable=False)  # sms, email, upi_notification
    raw_message = Column(Text, nullable=False)
    parsed_data = Column(JSON)
    voucher_id = Column(String, ForeignKey("vouchers.id"))
    status = Column(String(20), default="pending")  # pending, auto_posted, confirmed, rejected
    confidence_score = Column(Float)
    matched_ledger = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)
