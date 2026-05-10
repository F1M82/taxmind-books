-- =============================================================================
-- TaxMind Books — Database Schema (PostgreSQL 15+)
-- =============================================================================
-- Status: Frozen as of v1.1.
--
-- This file is the authoritative DDL. The SQLAlchemy models in
-- backend/app/models/ MUST produce a schema identical to this. Alembic's
-- autogenerate is used to keep them in sync; deviations are caught in CI.
--
-- Coder Claude consults this file before writing any model. New columns,
-- indexes, or constraints are proposed here first, then implemented.
--
-- Conventions:
--   - All primary keys: UUID v4
--   - All money columns: NUMERIC(15, 2) — see MONEY.md
--   - All timestamps: TIMESTAMPTZ, NOT NULL, server default NOW()
--   - All FKs: explicit ON DELETE behavior (RESTRICT for financial entities,
--     CASCADE for owned children, SET NULL for nullable references)
--   - Indexes named: idx_{table}_{cols}
--   - Unique constraints named: uq_{table}_{cols}
--   - Check constraints named: ck_{table}_{purpose}
-- =============================================================================

-- Required extensions ---------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- for fuzzy ledger name matching

-- Enum types ------------------------------------------------------------------

CREATE TYPE company_status AS ENUM ('active', 'inactive', 'suspended');

CREATE TYPE voucher_status AS ENUM (
    'draft',
    'pending_approval',
    'optional',
    'posted',
    'cancelled',
    'rejected_optional'
);

CREATE TYPE voucher_type AS ENUM (
    'Receipt',
    'Payment',
    'Sales',
    'Purchase',
    'Journal',
    'Contra',
    'Debit Note',
    'Credit Note'
);

CREATE TYPE entry_type AS ENUM ('Dr', 'Cr');

CREATE TYPE balance_type AS ENUM ('Dr', 'Cr');

CREATE TYPE company_role AS ENUM ('owner', 'admin', 'accountant', 'viewer');

CREATE TYPE ingestion_source AS ENUM (
    'whatsapp',
    'sms',
    'email',
    'photo',
    'pdf',
    'csv',
    'voice',
    'manual',
    'tally_sync'
);

CREATE TYPE ingestion_status AS ENUM (
    'received',
    'extracting',
    'extracted',
    'matching',
    'matched',
    'review',
    'posted',
    'failed',
    'discarded'
);

CREATE TYPE recon_status AS ENUM (
    'processing',
    'completed',
    'partial',
    'failed'
);

CREATE TYPE match_status AS ENUM (
    'pending',
    'auto_matched',
    'user_confirmed',
    'disputed',
    'rejected'
);

CREATE TYPE match_tier AS ENUM (
    'tier_1_exact',
    'tier_2_ref_amount_date_fuzzy',
    'tier_3_fuzzy_ref',
    'tier_4_amount_date',
    'tier_5_amount_only',
    'tier_6_unmatched'
);


-- =============================================================================
-- 1. users
-- -----------------------------------------------------------------------------
-- Global authentication identity. NOT tenant-scoped; a user may belong to
-- multiple companies via user_companies.
-- =============================================================================

CREATE TABLE users (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email             VARCHAR(255) NOT NULL,
    hashed_password   VARCHAR(255) NOT NULL,
    full_name         VARCHAR(255) NOT NULL,
    phone             VARCHAR(15),
    is_ca             BOOLEAN NOT NULL DEFAULT FALSE,
    firm_name         VARCHAR(255),
    ca_membership_no  VARCHAR(50),
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    is_superuser      BOOLEAN NOT NULL DEFAULT FALSE,
    last_login_at     TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT ck_users_email_lowercase CHECK (email = LOWER(email)),
    CONSTRAINT ck_users_phone_format CHECK (phone IS NULL OR phone ~ '^[+]?[0-9]{10,15}$')
);

CREATE INDEX idx_users_email ON users (email);
CREATE INDEX idx_users_active ON users (is_active) WHERE is_active = TRUE;


-- =============================================================================
-- 2. companies
-- -----------------------------------------------------------------------------
-- The tenant root. Every financial entity is scoped to a company.
-- =============================================================================

CREATE TABLE companies (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                     VARCHAR(255) NOT NULL,
    gstin                    VARCHAR(15),
    pan                      VARCHAR(10),
    financial_year_start     DATE NOT NULL DEFAULT '2026-04-01',
    accounting_source        VARCHAR(50) NOT NULL DEFAULT 'standalone',
    status                   company_status NOT NULL DEFAULT 'active',
    address                  TEXT,
    city                     VARCHAR(100),
    state_code               VARCHAR(2),
    pincode                  VARCHAR(6),
    created_by               UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_companies_gstin UNIQUE (gstin),
    CONSTRAINT ck_companies_gstin_format CHECK (
        gstin IS NULL OR gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'
    ),
    CONSTRAINT ck_companies_pan_format CHECK (
        pan IS NULL OR pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]{1}$'
    ),
    CONSTRAINT ck_companies_pincode_format CHECK (
        pincode IS NULL OR pincode ~ '^[0-9]{6}$'
    ),
    CONSTRAINT ck_companies_state_code_format CHECK (
        state_code IS NULL OR state_code ~ '^[0-9]{2}$'
    ),
    CONSTRAINT ck_companies_fy_start_april CHECK (
        EXTRACT(MONTH FROM financial_year_start) = 4
        AND EXTRACT(DAY FROM financial_year_start) = 1
    ),
    CONSTRAINT ck_companies_accounting_source CHECK (
        accounting_source IN ('standalone', 'tally', 'zoho', 'quickbooks', 'busy')
    )
);

CREATE INDEX idx_companies_status ON companies (status) WHERE status = 'active';
CREATE INDEX idx_companies_gstin ON companies (gstin) WHERE gstin IS NOT NULL;


-- =============================================================================
-- 3. user_companies (membership; many-to-many users <-> companies, with role)
-- =============================================================================

CREATE TABLE user_companies (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_id   UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    role         company_role NOT NULL DEFAULT 'viewer',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_user_companies_user_company UNIQUE (user_id, company_id)
);

CREATE INDEX idx_user_companies_user ON user_companies (user_id);
CREATE INDEX idx_user_companies_company ON user_companies (company_id);


-- =============================================================================
-- 4. ledgers (chart of accounts; mirror of Tally ledgers)
-- -----------------------------------------------------------------------------
-- Tenant-scoped. One row per ledger master per company. Synced from Tally
-- on connector handshake; updated when Tally sync delivers changes.
-- =============================================================================

CREATE TABLE ledgers (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id           UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    name                 VARCHAR(255) NOT NULL,
    name_normalized      VARCHAR(255) NOT NULL,
    group_name           VARCHAR(100),
    parent_ledger_id     UUID REFERENCES ledgers(id) ON DELETE SET NULL,
    opening_balance      NUMERIC(15, 2) NOT NULL DEFAULT 0,
    balance_type         balance_type NOT NULL DEFAULT 'Dr',
    gstin                VARCHAR(15),
    pan                  VARCHAR(10),
    phone                VARCHAR(15),
    email                VARCHAR(255),
    address              TEXT,
    state_code           VARCHAR(2),
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    tally_master_id      VARCHAR(100),
    tally_synced_at      TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_ledgers_company_name UNIQUE (company_id, name),
    CONSTRAINT uq_ledgers_company_tally UNIQUE (company_id, tally_master_id),
    CONSTRAINT ck_ledgers_gstin_format CHECK (
        gstin IS NULL OR gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'
    ),
    CONSTRAINT ck_ledgers_pan_format CHECK (
        pan IS NULL OR pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]{1}$'
    )
);

CREATE INDEX idx_ledgers_company ON ledgers (company_id);
CREATE INDEX idx_ledgers_company_active ON ledgers (company_id, is_active);
CREATE INDEX idx_ledgers_company_group ON ledgers (company_id, group_name);
CREATE INDEX idx_ledgers_gstin ON ledgers (company_id, gstin) WHERE gstin IS NOT NULL;
CREATE INDEX idx_ledgers_pan ON ledgers (company_id, pan) WHERE pan IS NOT NULL;
CREATE INDEX idx_ledgers_name_trgm ON ledgers USING gin (name_normalized gin_trgm_ops);


-- =============================================================================
-- 5. vouchers (the financial heart of the system)
-- -----------------------------------------------------------------------------
-- Tenant-scoped. Soft-delete via status='cancelled' only; no hard delete.
-- =============================================================================

CREATE TABLE vouchers (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id             UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    voucher_type           voucher_type NOT NULL,
    voucher_number         VARCHAR(50),
    date                   DATE NOT NULL,
    narration              TEXT,
    reference              VARCHAR(100),
    total_amount           NUMERIC(15, 2) NOT NULL,
    status                 voucher_status NOT NULL DEFAULT 'posted',
    source                 VARCHAR(20) NOT NULL DEFAULT 'manual',
    source_ingestion_id    UUID,
    is_auto_posted         BOOLEAN NOT NULL DEFAULT FALSE,
    confidence_score       NUMERIC(4, 3),

    -- GST
    gst_applicable         BOOLEAN NOT NULL DEFAULT FALSE,
    place_of_supply        VARCHAR(2),
    cgst                   NUMERIC(15, 2) NOT NULL DEFAULT 0,
    sgst                   NUMERIC(15, 2) NOT NULL DEFAULT 0,
    igst                   NUMERIC(15, 2) NOT NULL DEFAULT 0,
    cess                   NUMERIC(15, 2) NOT NULL DEFAULT 0,

    -- TDS
    tds_applicable         BOOLEAN NOT NULL DEFAULT FALSE,
    tds_amount             NUMERIC(15, 2) NOT NULL DEFAULT 0,
    tds_section            VARCHAR(10),

    -- Tally posting
    tally_posted_at        TIMESTAMPTZ,
    tally_voucher_guid     VARCHAR(100),
    tally_post_attempts    INTEGER NOT NULL DEFAULT 0,
    tally_last_error       TEXT,

    -- v1.2: Optional voucher flow
    is_optional_in_tally       BOOLEAN NOT NULL DEFAULT FALSE,
    approved_to_regular_at     TIMESTAMPTZ,
    approved_to_regular_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    optional_rejection_reason  TEXT,
    optional_rejected_at       TIMESTAMPTZ,
    optional_rejected_by       UUID REFERENCES users(id) ON DELETE SET NULL,

    created_by             UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_by            UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_at            TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_vouchers_company_number_type UNIQUE (company_id, voucher_type, voucher_number)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_vouchers_total_positive CHECK (total_amount >= 0),
    CONSTRAINT ck_vouchers_confidence_range CHECK (
        confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)
    ),
    CONSTRAINT ck_vouchers_source CHECK (
        source IN ('manual', 'whatsapp', 'sms', 'email', 'photo', 'pdf',
                   'csv', 'voice', 'tally_sync', 'recon')
    ),
    CONSTRAINT ck_vouchers_gst_components CHECK (
        cgst >= 0 AND sgst >= 0 AND igst >= 0 AND cess >= 0
    ),
    CONSTRAINT ck_vouchers_tds CHECK (
        tds_amount >= 0
        AND (NOT tds_applicable OR tds_section IS NOT NULL)
    ),
    CONSTRAINT ck_vouchers_place_of_supply CHECK (
        place_of_supply IS NULL OR place_of_supply ~ '^[0-9]{2}$'
    )
);

CREATE INDEX idx_vouchers_company_date ON vouchers (company_id, date DESC);
CREATE INDEX idx_vouchers_company_status ON vouchers (company_id, status);
CREATE INDEX idx_vouchers_company_type_date ON vouchers (company_id, voucher_type, date DESC);
CREATE INDEX idx_vouchers_source_ingestion ON vouchers (source_ingestion_id)
    WHERE source_ingestion_id IS NOT NULL;
CREATE INDEX idx_vouchers_unposted_to_tally ON vouchers (company_id)
    WHERE status = 'posted' AND tally_posted_at IS NULL;
-- v1.2: Optional vouchers awaiting approval
CREATE INDEX idx_vouchers_optional_pending ON vouchers (company_id, date DESC)
    WHERE is_optional_in_tally = TRUE AND approved_to_regular_at IS NULL
      AND status NOT IN ('cancelled', 'rejected_optional');


-- =============================================================================
-- 6. ledger_entries (Dr/Cr lines under a voucher)
-- =============================================================================

CREATE TABLE ledger_entries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    voucher_id      UUID NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
    ledger_id       UUID NOT NULL REFERENCES ledgers(id) ON DELETE RESTRICT,
    amount          NUMERIC(15, 2) NOT NULL,
    entry_type      entry_type NOT NULL,
    line_number     INTEGER NOT NULL,
    narration       TEXT,
    gst_rate        NUMERIC(5, 2),
    cgst            NUMERIC(15, 2),
    sgst            NUMERIC(15, 2),
    igst            NUMERIC(15, 2),
    tds_amount      NUMERIC(15, 2),
    tds_section     VARCHAR(10),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_ledger_entries_voucher_line UNIQUE (voucher_id, line_number),
    CONSTRAINT ck_ledger_entries_amount_positive CHECK (amount > 0),
    CONSTRAINT ck_ledger_entries_gst_rate CHECK (
        gst_rate IS NULL OR (gst_rate >= 0 AND gst_rate <= 100)
    )
);

CREATE INDEX idx_ledger_entries_voucher ON ledger_entries (voucher_id);
CREATE INDEX idx_ledger_entries_ledger ON ledger_entries (ledger_id);
CREATE INDEX idx_ledger_entries_company_ledger ON ledger_entries (company_id, ledger_id);


-- =============================================================================
-- 7. ingestions (raw inbound captures, before extraction)
-- =============================================================================

CREATE TABLE ingestions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id        UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    source            ingestion_source NOT NULL,
    status            ingestion_status NOT NULL DEFAULT 'received',
    raw_payload       JSONB,
    s3_object_key     VARCHAR(500),
    content_type      VARCHAR(100),
    content_size      BIGINT,
    sender_identifier VARCHAR(255),
    received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    extracted_at      TIMESTAMPTZ,
    failed_at         TIMESTAMPTZ,
    failure_reason    TEXT,
    idempotency_key   VARCHAR(255),
    posted_voucher_id UUID REFERENCES vouchers(id) ON DELETE SET NULL,  -- v1.2
    parsed_data       JSONB,                                            -- v1.2
    extraction_confidence NUMERIC(4, 3),                                -- v1.2
    extraction_flags  JSONB NOT NULL DEFAULT '[]'::JSONB,               -- v1.2
    created_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_ingestions_company_idempotency UNIQUE (company_id, idempotency_key),
    CONSTRAINT ck_ingestions_size_positive CHECK (content_size IS NULL OR content_size > 0),
    CONSTRAINT ck_ingestions_extraction_confidence CHECK (
        extraction_confidence IS NULL OR (extraction_confidence >= 0 AND extraction_confidence <= 1)
    )
);

CREATE INDEX idx_ingestions_company_status ON ingestions (company_id, status);
CREATE INDEX idx_ingestions_company_received ON ingestions (company_id, received_at DESC);
CREATE INDEX idx_ingestions_pending ON ingestions (status)
    WHERE status IN ('received', 'extracting', 'matching');


-- =============================================================================
-- 8. (REMOVED in v1.2) draft_vouchers
-- -----------------------------------------------------------------------------
-- The draft_vouchers table has been removed in v1.2. Per the Flow B design,
-- AI-extracted entries land directly in the vouchers table with
-- is_optional_in_tally = TRUE, and they are posted to Tally as Optional
-- vouchers. The "draft" concept is replaced by the "Optional in Tally" state.
--
-- See AMENDMENTS_v1.2.md for the rationale and EXTRACTION_CONTRACT.md for the
-- updated flow.
-- =============================================================================


-- =============================================================================
-- 9. reconciliations (debtor/creditor recon sessions)
-- =============================================================================

CREATE TABLE reconciliations (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id               UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    party_ledger_id          UUID REFERENCES ledgers(id) ON DELETE SET NULL,
    party_name               VARCHAR(255) NOT NULL,
    party_gstin              VARCHAR(15),
    period_from              DATE NOT NULL,
    period_to                DATE NOT NULL,
    status                   recon_status NOT NULL DEFAULT 'processing',
    your_balance             NUMERIC(15, 2),
    party_balance            NUMERIC(15, 2),
    difference               NUMERIC(15, 2),
    matched_count            INTEGER NOT NULL DEFAULT 0,
    fuzzy_count              INTEGER NOT NULL DEFAULT 0,
    missing_in_party_count   INTEGER NOT NULL DEFAULT 0,
    missing_in_books_count   INTEGER NOT NULL DEFAULT 0,
    party_statement_s3_key   VARCHAR(500),
    certificate_s3_key       VARCHAR(500),
    created_by               UUID REFERENCES users(id) ON DELETE SET NULL,
    completed_at             TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_reconciliations_period_order CHECK (period_to >= period_from),
    CONSTRAINT ck_reconciliations_counts_nonnegative CHECK (
        matched_count >= 0 AND fuzzy_count >= 0
        AND missing_in_party_count >= 0 AND missing_in_books_count >= 0
    )
);

CREATE INDEX idx_reconciliations_company_created ON reconciliations (company_id, created_at DESC);
CREATE INDEX idx_reconciliations_party ON reconciliations (company_id, party_ledger_id)
    WHERE party_ledger_id IS NOT NULL;


-- =============================================================================
-- 10. recon_matches (tier-classified matches inside a reconciliation)
-- =============================================================================

CREATE TABLE recon_matches (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id                  UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    reconciliation_id           UUID NOT NULL REFERENCES reconciliations(id) ON DELETE CASCADE,
    your_voucher_id             UUID REFERENCES vouchers(id) ON DELETE SET NULL,
    party_transaction_data      JSONB,
    your_transaction_data       JSONB,
    tier                        match_tier NOT NULL,
    status                      match_status NOT NULL DEFAULT 'pending',
    confidence_score            NUMERIC(4, 3) NOT NULL,
    difference                  NUMERIC(15, 2) NOT NULL DEFAULT 0,
    flags                       JSONB NOT NULL DEFAULT '[]'::JSONB,
    suggested_action            TEXT,
    user_notes                  TEXT,
    confirmed_by                UUID REFERENCES users(id) ON DELETE SET NULL,
    confirmed_at                TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_recon_matches_confidence_range CHECK (
        confidence_score >= 0 AND confidence_score <= 1
    )
);

CREATE INDEX idx_recon_matches_recon ON recon_matches (reconciliation_id);
CREATE INDEX idx_recon_matches_status ON recon_matches (reconciliation_id, status);
CREATE INDEX idx_recon_matches_tier ON recon_matches (reconciliation_id, tier);


-- =============================================================================
-- 11. audit_logs (append-only; see AUDIT.md)
-- -----------------------------------------------------------------------------
-- company_id is NULLABLE so global / system events (user.created,
-- user.password_changed, user.deactivated, etc.) — which have no tenant
-- scope at the time they occur — can still be audited as required by
-- AUDIT.md §"What is financially significant".
--
-- Tenant-scoped queries from the audit-log read API filter explicitly
-- on `company_id = <active>` and so naturally exclude NULL rows. System
-- rows are accessible only via admin/superuser paths (Phase 5+).
-- See docs/AMENDMENTS_v1.2.md §"Patch 1".
-- =============================================================================

CREATE TABLE audit_logs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id    UUID REFERENCES companies(id) ON DELETE RESTRICT,
    user_id       UUID REFERENCES users(id) ON DELETE SET NULL,
    action        VARCHAR(40) NOT NULL,
    entity_type   VARCHAR(40) NOT NULL,
    entity_id     UUID NOT NULL,
    old_value     JSONB,
    new_value     JSONB,
    changes       JSONB NOT NULL DEFAULT '{}'::JSONB,
    ip_address    INET,
    user_agent    TEXT,
    request_id    UUID,
    source        VARCHAR(20) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_audit_logs_source CHECK (
        source IN ('api', 'worker', 'connector', 'system')
    )
);

-- Partial index: company-scoped queries only need rows that belong to
-- a tenant. System-event rows (company_id IS NULL) are reached via
-- admin paths that don't use this index.
CREATE INDEX idx_audit_logs_company_created ON audit_logs (company_id, created_at DESC)
    WHERE company_id IS NOT NULL;
CREATE INDEX idx_audit_logs_entity ON audit_logs (entity_type, entity_id);
CREATE INDEX idx_audit_logs_user ON audit_logs (user_id, created_at DESC)
    WHERE user_id IS NOT NULL;
CREATE INDEX idx_audit_logs_request ON audit_logs (request_id) WHERE request_id IS NOT NULL;

-- Append-only enforcement (Layer 2 from AUDIT.md)
CREATE OR REPLACE FUNCTION prevent_audit_modification() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only — UPDATE and DELETE are forbidden';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_logs_no_update BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();

CREATE TRIGGER audit_logs_no_delete BEFORE DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();


-- =============================================================================
-- 12. sms_templates (global library; not tenant-scoped)
-- =============================================================================

CREATE TABLE sms_templates (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bank_name          VARCHAR(50) NOT NULL,
    template_pattern   TEXT NOT NULL,
    example_message    TEXT NOT NULL,
    extraction_fields  JSONB NOT NULL,
    priority           INTEGER NOT NULL DEFAULT 100,
    version            INTEGER NOT NULL DEFAULT 1,
    is_active          BOOLEAN NOT NULL DEFAULT FALSE,
    created_by         UUID REFERENCES users(id) ON DELETE SET NULL,
    activated_by       UUID REFERENCES users(id) ON DELETE SET NULL,
    activated_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_sms_templates_bank_version UNIQUE (bank_name, version)
);

CREATE INDEX idx_sms_templates_active_priority ON sms_templates (is_active, priority)
    WHERE is_active = TRUE;


-- =============================================================================
-- 13. narration_rules (per-company learned bank-narration → ledger map)
-- =============================================================================

CREATE TABLE narration_rules (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id          UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    pattern             TEXT NOT NULL,
    pattern_type        VARCHAR(20) NOT NULL DEFAULT 'substring',
    ledger_id           UUID NOT NULL REFERENCES ledgers(id) ON DELETE CASCADE,
    voucher_type_hint   voucher_type,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    match_count         INTEGER NOT NULL DEFAULT 0,
    last_matched_at     TIMESTAMPTZ,
    created_by          UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_narration_rules_pattern_type CHECK (
        pattern_type IN ('substring', 'regex', 'exact')
    )
);

CREATE INDEX idx_narration_rules_company_active ON narration_rules (company_id, is_active);
CREATE INDEX idx_narration_rules_ledger ON narration_rules (ledger_id);


-- =============================================================================
-- 14. idempotency_keys (request deduplication; see IDEMPOTENCY.md)
-- =============================================================================

CREATE TABLE idempotency_keys (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id        UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id           UUID REFERENCES users(id) ON DELETE CASCADE,
    key               VARCHAR(255) NOT NULL,
    method            VARCHAR(10) NOT NULL,
    path              VARCHAR(500) NOT NULL,
    request_hash      VARCHAR(64) NOT NULL,
    response_status   INTEGER,
    response_body     JSONB,
    response_headers  JSONB,
    locked_at         TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at        TIMESTAMPTZ NOT NULL,

    CONSTRAINT uq_idempotency_keys_company_key UNIQUE (company_id, key)
);

CREATE INDEX idx_idempotency_keys_expires ON idempotency_keys (expires_at);


-- =============================================================================
-- Updated-at triggers
-- -----------------------------------------------------------------------------
-- Every table with an updated_at column gets a trigger to bump it.
-- =============================================================================

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_companies_updated_at BEFORE UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_user_companies_updated_at BEFORE UPDATE ON user_companies
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_ledgers_updated_at BEFORE UPDATE ON ledgers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_vouchers_updated_at BEFORE UPDATE ON vouchers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_ingestions_updated_at BEFORE UPDATE ON ingestions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_draft_vouchers_updated_at BEFORE UPDATE ON draft_vouchers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_reconciliations_updated_at BEFORE UPDATE ON reconciliations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_sms_templates_updated_at BEFORE UPDATE ON sms_templates
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_narration_rules_updated_at BEFORE UPDATE ON narration_rules
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================================
-- v1.2 — Additional tables
-- =============================================================================

-- 15. extraction_quotas (rate limiting AI extraction per company per day)
CREATE TABLE extraction_quotas (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id               UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    date                     DATE NOT NULL,
    extraction_count         INTEGER NOT NULL DEFAULT 0,
    extraction_token_total   BIGINT NOT NULL DEFAULT 0,
    soft_limit_alerted       BOOLEAN NOT NULL DEFAULT FALSE,
    hard_limit_hit_at        TIMESTAMPTZ,
    soft_limit_count         INTEGER NOT NULL DEFAULT 100,
    soft_limit_tokens        BIGINT NOT NULL DEFAULT 500000,
    hard_limit_count         INTEGER NOT NULL DEFAULT 500,
    hard_limit_tokens        BIGINT NOT NULL DEFAULT 2000000,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_extraction_quotas_company_date UNIQUE (company_id, date)
);

CREATE INDEX idx_extraction_quotas_company_date ON extraction_quotas (company_id, date DESC);


-- 16. cost_tracking (per-company per-month cost rollup)
CREATE TABLE cost_tracking (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id               UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    month                    DATE NOT NULL,                  -- first day of month
    llm_extraction_count     INTEGER NOT NULL DEFAULT 0,
    llm_input_tokens         BIGINT NOT NULL DEFAULT 0,
    llm_output_tokens        BIGINT NOT NULL DEFAULT 0,
    llm_cost_usd             NUMERIC(10, 4) NOT NULL DEFAULT 0,
    storage_gb               NUMERIC(10, 4) NOT NULL DEFAULT 0,
    storage_cost_usd         NUMERIC(10, 4) NOT NULL DEFAULT 0,
    compute_seconds          BIGINT NOT NULL DEFAULT 0,
    compute_cost_usd         NUMERIC(10, 4) NOT NULL DEFAULT 0,
    total_cost_usd           NUMERIC(10, 4) NOT NULL DEFAULT 0,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_cost_tracking_company_month UNIQUE (company_id, month),
    CONSTRAINT ck_cost_tracking_month_first CHECK (
        EXTRACT(DAY FROM month) = 1
    )
);

CREATE INDEX idx_cost_tracking_company_month ON cost_tracking (company_id, month DESC);


-- 17. account_deletion_requests (DPDP Act compliance)
CREATE TYPE account_deletion_status AS ENUM (
    'grace_period',
    'cancelled',
    'processing',
    'completed',
    'failed'
);

CREATE TABLE account_deletion_requests (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    requested_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    grace_ends_at            TIMESTAMPTZ NOT NULL,
    status                   account_deletion_status NOT NULL DEFAULT 'grace_period',
    cancelled_at             TIMESTAMPTZ,
    processing_started_at    TIMESTAMPTZ,
    completed_at             TIMESTAMPTZ,
    failure_reason           TEXT,
    final_export_s3_key      VARCHAR(500),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_account_deletion_user ON account_deletion_requests (user_id);
CREATE INDEX idx_account_deletion_grace_pending ON account_deletion_requests (grace_ends_at)
    WHERE status = 'grace_period';


-- 18. data_export_requests (DPDP Act compliance)
CREATE TYPE data_export_status AS ENUM (
    'pending',
    'processing',
    'completed',
    'failed'
);

CREATE TABLE data_export_requests (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_id          UUID REFERENCES companies(id) ON DELETE CASCADE,
    requested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status              data_export_status NOT NULL DEFAULT 'pending',
    s3_object_key       VARCHAR(500),
    download_url        VARCHAR(2000),
    download_url_expires_at TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    failure_reason      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_data_export_user_status ON data_export_requests (user_id, status);


-- 19. device_tokens (push notification — FCM/APNs)
CREATE TYPE device_platform AS ENUM ('android', 'ios', 'web');

CREATE TABLE device_tokens (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token               VARCHAR(500) NOT NULL,
    platform            device_platform NOT NULL,
    app_version         VARCHAR(50),
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    last_active_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_device_tokens_token UNIQUE (token)
);

CREATE INDEX idx_device_tokens_user_active ON device_tokens (user_id, is_active);


-- v1.2: updated_at triggers for new tables
CREATE TRIGGER trg_extraction_quotas_updated_at BEFORE UPDATE ON extraction_quotas
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_cost_tracking_updated_at BEFORE UPDATE ON cost_tracking
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_account_deletion_updated_at BEFORE UPDATE ON account_deletion_requests
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_data_export_updated_at BEFORE UPDATE ON data_export_requests
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_device_tokens_updated_at BEFORE UPDATE ON device_tokens
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================================
-- Application role grants (for production; see AUDIT.md Layer 1)
-- -----------------------------------------------------------------------------
-- These statements are NOT run by Alembic. They are applied during deployment
-- by the DB admin runbook. Listed here as the authoritative reference.
-- =============================================================================

-- CREATE ROLE taxmind_app LOGIN PASSWORD '...';
-- GRANT CONNECT ON DATABASE taxmind_books TO taxmind_app;
-- GRANT USAGE ON SCHEMA public TO taxmind_app;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO taxmind_app;
-- REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM taxmind_app;
-- GRANT SELECT, INSERT ON audit_logs TO taxmind_app;
-- GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO taxmind_app;


-- =============================================================================
-- End of schema
-- =============================================================================
