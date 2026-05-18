--
-- PostgreSQL database dump
--

\restrict nzufTnBeDBXruV4bJyXPQKy5jJWiqtENX4x9aQapIoiplPYUdRTuyV9QUG4kDh2

-- Dumped from database version 16.13
-- Dumped by pg_dump version 16.13

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: account_deletion_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.account_deletion_status AS ENUM (
    'grace_period',
    'cancelled',
    'processing',
    'completed',
    'failed'
);


--
-- Name: balance_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.balance_type AS ENUM (
    'Dr',
    'Cr'
);


--
-- Name: company_role; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.company_role AS ENUM (
    'owner',
    'admin',
    'accountant',
    'viewer'
);


--
-- Name: company_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.company_status AS ENUM (
    'active',
    'inactive',
    'suspended'
);


--
-- Name: device_platform; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.device_platform AS ENUM (
    'android',
    'ios',
    'web'
);


--
-- Name: entry_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.entry_type AS ENUM (
    'Dr',
    'Cr'
);


--
-- Name: voucher_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.voucher_status AS ENUM (
    'draft',
    'pending_approval',
    'posted',
    'cancelled',
    'optional',
    'rejected_optional'
);


--
-- Name: voucher_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.voucher_type AS ENUM (
    'Receipt',
    'Payment',
    'Sales',
    'Purchase',
    'Journal',
    'Contra',
    'Debit Note',
    'Credit Note'
);


--
-- Name: prevent_audit_modification(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.prevent_audit_modification() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only — UPDATE and DELETE are forbidden';
        END;
        $$;


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: account_deletion_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.account_deletion_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    grace_ends_at timestamp with time zone NOT NULL,
    status public.account_deletion_status DEFAULT 'grace_period'::public.account_deletion_status NOT NULL,
    cancelled_at timestamp with time zone,
    processing_started_at timestamp with time zone,
    completed_at timestamp with time zone,
    failure_reason text,
    final_export_s3_key character varying(500),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    company_id uuid,
    user_id uuid,
    action character varying(40) NOT NULL,
    entity_type character varying(40) NOT NULL,
    entity_id uuid NOT NULL,
    old_value jsonb,
    new_value jsonb,
    changes jsonb DEFAULT '{}'::jsonb NOT NULL,
    ip_address inet,
    user_agent text,
    request_id uuid,
    source character varying(20) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_audit_logs_source CHECK (((source)::text = ANY ((ARRAY['api'::character varying, 'worker'::character varying, 'connector'::character varying, 'system'::character varying])::text[])))
);


--
-- Name: companies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.companies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(255) NOT NULL,
    gstin character varying(15),
    pan character varying(10),
    financial_year_start date DEFAULT '2026-04-01'::date NOT NULL,
    accounting_source character varying(50) DEFAULT 'standalone'::character varying NOT NULL,
    status public.company_status DEFAULT 'active'::public.company_status NOT NULL,
    address text,
    city character varying(100),
    state_code character varying(2),
    pincode character varying(6),
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_companies_accounting_source CHECK (((accounting_source)::text = ANY ((ARRAY['standalone'::character varying, 'tally'::character varying, 'zoho'::character varying, 'quickbooks'::character varying, 'busy'::character varying])::text[]))),
    CONSTRAINT ck_companies_fy_start_april CHECK (((EXTRACT(month FROM financial_year_start) = (4)::numeric) AND (EXTRACT(day FROM financial_year_start) = (1)::numeric))),
    CONSTRAINT ck_companies_gstin_format CHECK (((gstin IS NULL) OR ((gstin)::text ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'::text))),
    CONSTRAINT ck_companies_pan_format CHECK (((pan IS NULL) OR ((pan)::text ~ '^[A-Z]{5}[0-9]{4}[A-Z]{1}$'::text))),
    CONSTRAINT ck_companies_pincode_format CHECK (((pincode IS NULL) OR ((pincode)::text ~ '^[0-9]{6}$'::text))),
    CONSTRAINT ck_companies_state_code_format CHECK (((state_code IS NULL) OR ((state_code)::text ~ '^[0-9]{2}$'::text)))
);


--
-- Name: connector_enrollment_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.connector_enrollment_codes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    company_id uuid NOT NULL,
    created_by uuid,
    code_hash character varying(64) NOT NULL,
    consumed_at timestamp with time zone,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: device_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.device_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token character varying(500) NOT NULL,
    platform public.device_platform NOT NULL,
    app_version character varying(50),
    is_active boolean DEFAULT true NOT NULL,
    last_active_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: idempotency_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.idempotency_keys (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    company_id uuid NOT NULL,
    user_id uuid,
    key character varying(255) NOT NULL,
    method character varying(10) NOT NULL,
    path character varying(500) NOT NULL,
    request_hash character varying(64) NOT NULL,
    response_status integer,
    response_body jsonb,
    response_headers jsonb,
    locked_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: ledger_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ledger_entries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    company_id uuid NOT NULL,
    voucher_id uuid NOT NULL,
    ledger_id uuid NOT NULL,
    amount numeric(15,2) NOT NULL,
    entry_type public.entry_type NOT NULL,
    line_number integer NOT NULL,
    narration text,
    gst_rate numeric(5,2),
    cgst numeric(15,2),
    sgst numeric(15,2),
    igst numeric(15,2),
    tds_amount numeric(15,2),
    tds_section character varying(10),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_ledger_entries_amount_positive CHECK ((amount > (0)::numeric)),
    CONSTRAINT ck_ledger_entries_gst_rate CHECK (((gst_rate IS NULL) OR ((gst_rate >= (0)::numeric) AND (gst_rate <= (100)::numeric))))
);


--
-- Name: ledgers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ledgers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    company_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    name_normalized character varying(255) NOT NULL,
    group_name character varying(100),
    parent_ledger_id uuid,
    opening_balance numeric(15,2) DEFAULT 0 NOT NULL,
    balance_type public.balance_type DEFAULT 'Dr'::public.balance_type NOT NULL,
    gstin character varying(15),
    pan character varying(10),
    phone character varying(15),
    email character varying(255),
    address text,
    state_code character varying(2),
    is_active boolean DEFAULT true NOT NULL,
    tally_master_id character varying(100),
    tally_synced_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_ledgers_gstin_format CHECK (((gstin IS NULL) OR ((gstin)::text ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'::text))),
    CONSTRAINT ck_ledgers_pan_format CHECK (((pan IS NULL) OR ((pan)::text ~ '^[A-Z]{5}[0-9]{4}[A-Z]{1}$'::text)))
);


--
-- Name: user_companies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_companies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    company_id uuid NOT NULL,
    role public.company_role DEFAULT 'viewer'::public.company_role NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email character varying(255) NOT NULL,
    hashed_password character varying(255) NOT NULL,
    full_name character varying(255) NOT NULL,
    phone character varying(15),
    is_ca boolean DEFAULT false NOT NULL,
    firm_name character varying(255),
    ca_membership_no character varying(50),
    is_active boolean DEFAULT true NOT NULL,
    is_superuser boolean DEFAULT false NOT NULL,
    last_login_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_users_email_lowercase CHECK (((email)::text = lower((email)::text))),
    CONSTRAINT ck_users_phone_format CHECK (((phone IS NULL) OR ((phone)::text ~ '^[+]?[0-9]{10,15}$'::text)))
);


--
-- Name: vouchers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vouchers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    company_id uuid NOT NULL,
    voucher_type public.voucher_type NOT NULL,
    voucher_number character varying(50),
    date date NOT NULL,
    narration text,
    reference character varying(100),
    total_amount numeric(15,2) NOT NULL,
    status public.voucher_status DEFAULT 'posted'::public.voucher_status NOT NULL,
    source character varying(20) DEFAULT 'manual'::character varying NOT NULL,
    source_ingestion_id uuid,
    is_auto_posted boolean DEFAULT false NOT NULL,
    confidence_score numeric(4,3),
    gst_applicable boolean DEFAULT false NOT NULL,
    place_of_supply character varying(2),
    cgst numeric(15,2) DEFAULT 0 NOT NULL,
    sgst numeric(15,2) DEFAULT 0 NOT NULL,
    igst numeric(15,2) DEFAULT 0 NOT NULL,
    cess numeric(15,2) DEFAULT 0 NOT NULL,
    tds_applicable boolean DEFAULT false NOT NULL,
    tds_amount numeric(15,2) DEFAULT 0 NOT NULL,
    tds_section character varying(10),
    tally_posted_at timestamp with time zone,
    tally_voucher_guid character varying(100),
    tally_post_attempts integer DEFAULT 0 NOT NULL,
    tally_last_error text,
    created_by uuid,
    approved_by uuid,
    approved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_optional_in_tally boolean DEFAULT false NOT NULL,
    approved_to_regular_at timestamp with time zone,
    approved_to_regular_by uuid,
    optional_rejection_reason text,
    optional_rejected_at timestamp with time zone,
    optional_rejected_by uuid,
    CONSTRAINT ck_vouchers_confidence_range CHECK (((confidence_score IS NULL) OR ((confidence_score >= (0)::numeric) AND (confidence_score <= (1)::numeric)))),
    CONSTRAINT ck_vouchers_gst_components CHECK (((cgst >= (0)::numeric) AND (sgst >= (0)::numeric) AND (igst >= (0)::numeric) AND (cess >= (0)::numeric))),
    CONSTRAINT ck_vouchers_place_of_supply CHECK (((place_of_supply IS NULL) OR ((place_of_supply)::text ~ '^[0-9]{2}$'::text))),
    CONSTRAINT ck_vouchers_source CHECK (((source)::text = ANY ((ARRAY['manual'::character varying, 'whatsapp'::character varying, 'sms'::character varying, 'email'::character varying, 'photo'::character varying, 'pdf'::character varying, 'csv'::character varying, 'voice'::character varying, 'tally_sync'::character varying, 'recon'::character varying])::text[]))),
    CONSTRAINT ck_vouchers_tds CHECK (((tds_amount >= (0)::numeric) AND ((NOT tds_applicable) OR (tds_section IS NOT NULL)))),
    CONSTRAINT ck_vouchers_total_positive CHECK ((total_amount >= (0)::numeric))
);


--
-- Data for Name: account_deletion_requests; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.account_deletion_requests (id, user_id, requested_at, grace_ends_at, status, cancelled_at, processing_started_at, completed_at, failure_reason, final_export_s3_key, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.alembic_version (version_num) FROM stdin;
0009
\.


--
-- Data for Name: audit_logs; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.audit_logs (id, company_id, user_id, action, entity_type, entity_id, old_value, new_value, changes, ip_address, user_agent, request_id, source, created_at) FROM stdin;
c3e290a3-f688-4e40-9457-a485a694962b	\N	79910b81-2a28-4fa4-8745-ee9d468a65bc	user.created	user	79910b81-2a28-4fa4-8745-ee9d468a65bc	null	{"id": "79910b81-2a28-4fa4-8745-ee9d468a65bc", "email": "test@taxmindbooks.dev", "is_ca": false, "firm_name": null, "full_name": "Test User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	Mozilla/5.0 (Windows NT; Windows NT 10.0; en-US) WindowsPowerShell/5.1.26100.8457	decc8ced-b16c-466c-a0e0-dfa3044c4324	api	2026-05-13 13:12:46.740129+00
2b2896aa-2724-44b2-8f3a-361c3bb05648	\N	ec471c79-a85d-4873-bd8d-ee08da19707e	user.created	user	ec471c79-a85d-4873-bd8d-ee08da19707e	null	{"id": "ec471c79-a85d-4873-bd8d-ee08da19707e", "email": "v7user-453b25aafc@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	144e5dde-e8d7-4718-9ebc-ad2b10d66628	api	2026-05-13 13:25:40.006946+00
b4e6bd63-8ec5-4a7e-a0f1-50ad32c5c012	e6ad6664-9dd8-4a58-93cb-da56e4cbd3aa	ec471c79-a85d-4873-bd8d-ee08da19707e	company.created	company	e6ad6664-9dd8-4a58-93cb-da56e4cbd3aa	null	{"id": "e6ad6664-9dd8-4a58-93cb-da56e4cbd3aa", "pan": null, "city": null, "name": "Acme-4a304b", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	23b84816-e657-4eb8-889e-0319f5c5b2b0	api	2026-05-13 13:25:40.382895+00
8fcce4e5-1194-495b-92bd-4bcaddcb423d	e6ad6664-9dd8-4a58-93cb-da56e4cbd3aa	ec471c79-a85d-4873-bd8d-ee08da19707e	ledger.created	ledger	b11e18fb-e3e3-44ec-8783-d264f2c679ce	null	{"id": "b11e18fb-e3e3-44ec-8783-d264f2c679ce", "pan": null, "name": "Bank-5d1a", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "e6ad6664-9dd8-4a58-93cb-da56e4cbd3aa", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-5d1a", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	18d1d615-612c-47c8-a55d-d49b69e6f582	api	2026-05-13 13:25:40.430305+00
91afa82a-31db-4926-9ff4-a0e3f89fc344	e6ad6664-9dd8-4a58-93cb-da56e4cbd3aa	ec471c79-a85d-4873-bd8d-ee08da19707e	ledger.created	ledger	abd8f149-65db-499f-ade1-7b28f364271e	null	{"id": "abd8f149-65db-499f-ade1-7b28f364271e", "pan": null, "name": "Party-5d5f", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "e6ad6664-9dd8-4a58-93cb-da56e4cbd3aa", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-5d5f", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	4ee7154c-ec6c-48d7-818d-3f36c1e46dbb	api	2026-05-13 13:25:40.46602+00
f494e59a-f0d3-415d-a169-126f991173b4	\N	85cda290-e587-4637-bc95-032c5fadfe1e	user.created	user	85cda290-e587-4637-bc95-032c5fadfe1e	null	{"id": "85cda290-e587-4637-bc95-032c5fadfe1e", "email": "v7user-650bc45132@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	3df45c71-b871-411d-af83-f2f3bf757675	api	2026-05-13 13:25:42.817325+00
c2f4482f-8807-4b0a-90a1-0e441d3cb359	07eed054-9a72-40bf-89bc-592568fd1d26	85cda290-e587-4637-bc95-032c5fadfe1e	company.created	company	07eed054-9a72-40bf-89bc-592568fd1d26	null	{"id": "07eed054-9a72-40bf-89bc-592568fd1d26", "pan": null, "city": null, "name": "Acme-228b91", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	0909f722-c26a-42d5-bdb9-f62a92335dd2	api	2026-05-13 13:25:43.192839+00
3212f4c6-e3b9-4cf6-a8ba-ebdd2fb9a7ed	07eed054-9a72-40bf-89bc-592568fd1d26	85cda290-e587-4637-bc95-032c5fadfe1e	ledger.created	ledger	74efb1df-8798-4da2-b16b-029163dec4dc	null	{"id": "74efb1df-8798-4da2-b16b-029163dec4dc", "pan": null, "name": "Bank-db0a", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "07eed054-9a72-40bf-89bc-592568fd1d26", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-db0a", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	45b821ee-ce2c-4a1b-aab1-f7c0cb0dca96	api	2026-05-13 13:25:43.206485+00
210e7216-6e95-4f3f-b86a-062149047eca	07eed054-9a72-40bf-89bc-592568fd1d26	85cda290-e587-4637-bc95-032c5fadfe1e	ledger.created	ledger	bbf818e8-a117-4eef-896c-f99c741e7736	null	{"id": "bbf818e8-a117-4eef-896c-f99c741e7736", "pan": null, "name": "Party-34f4", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "07eed054-9a72-40bf-89bc-592568fd1d26", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-34f4", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	2aed19bd-c735-45ea-b232-12cea1b97ec7	api	2026-05-13 13:25:43.218192+00
a7462773-7615-449e-94ea-35366c1a4514	07eed054-9a72-40bf-89bc-592568fd1d26	85cda290-e587-4637-bc95-032c5fadfe1e	voucher.created	voucher	6a113817-48c9-4f0e-a299-bb8cbb5e2386	null	{"id": "6a113817-48c9-4f0e-a299-bb8cbb5e2386", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "74efb1df-8798-4da2-b16b-029163dec4dc", "narration": null, "entry_type": "Dr", "line_number": 1}, {"amount": "1500.99", "ledger_id": "bbf818e8-a117-4eef-896c-f99c741e7736", "narration": null, "entry_type": "Cr", "line_number": 2}], "narration": "Validation suite test voucher", "reference": null, "company_id": "07eed054-9a72-40bf-89bc-592568fd1d26", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	39e9457b-881e-44fc-b60f-3e7e99ce5b51	api	2026-05-13 13:25:43.231599+00
333753de-2396-47f9-844e-57b006bf5d32	\N	05256d5b-fcc6-4c2c-a991-fd36d23d6504	user.created	user	05256d5b-fcc6-4c2c-a991-fd36d23d6504	null	{"id": "05256d5b-fcc6-4c2c-a991-fd36d23d6504", "email": "v7user-439faf3c9a@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	3406111e-b949-4a7d-8204-83056a4c02c3	api	2026-05-13 13:25:46.100334+00
ddba5a8e-c758-4bd0-a7fa-912d7a1a8513	69e53193-f10f-48d1-880f-e4745b39ed47	05256d5b-fcc6-4c2c-a991-fd36d23d6504	company.created	company	69e53193-f10f-48d1-880f-e4745b39ed47	null	{"id": "69e53193-f10f-48d1-880f-e4745b39ed47", "pan": null, "city": null, "name": "Acme-695d70", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	65bff8e2-414c-494a-8adb-42f005ad621f	api	2026-05-13 13:25:46.475853+00
aded01b7-a3a2-43ef-9479-eaaedf4ec6f9	69e53193-f10f-48d1-880f-e4745b39ed47	05256d5b-fcc6-4c2c-a991-fd36d23d6504	ledger.created	ledger	0218efa5-ec91-4ef2-b4cb-7a3b631f0ff3	null	{"id": "0218efa5-ec91-4ef2-b4cb-7a3b631f0ff3", "pan": null, "name": "Bank-082a", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "69e53193-f10f-48d1-880f-e4745b39ed47", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-082a", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	425462cb-65d0-4a56-bbdf-0cd6e8c5ec74	api	2026-05-13 13:25:46.49164+00
8aaaefe4-eeb2-4bcc-8eb9-f9688a441f13	69e53193-f10f-48d1-880f-e4745b39ed47	05256d5b-fcc6-4c2c-a991-fd36d23d6504	ledger.created	ledger	d0556e2f-f366-4e4b-a9c6-78c58cf09286	null	{"id": "d0556e2f-f366-4e4b-a9c6-78c58cf09286", "pan": null, "name": "Party-0796", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "69e53193-f10f-48d1-880f-e4745b39ed47", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-0796", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	e7ab1361-9ea4-4b9a-a6ac-2ea308ff5e71	api	2026-05-13 13:25:46.504898+00
6b8b828d-80d8-4c26-a0a1-a8745fb9c97f	\N	f7eaecff-8db0-466b-a734-74294dca8654	user.created	user	f7eaecff-8db0-466b-a734-74294dca8654	null	{"id": "f7eaecff-8db0-466b-a734-74294dca8654", "email": "v7user-3dd6144bf7@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	95383c42-8b81-4852-94b6-29757570cc28	api	2026-05-13 13:25:48.858087+00
01643c84-db60-4d92-a318-19b64ae5f373	a8eea1e3-8697-4bdd-a735-e0d1222816d6	f7eaecff-8db0-466b-a734-74294dca8654	company.created	company	a8eea1e3-8697-4bdd-a735-e0d1222816d6	null	{"id": "a8eea1e3-8697-4bdd-a735-e0d1222816d6", "pan": null, "city": null, "name": "Acme-b972b7", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	b51a971d-6b27-4229-8672-2f1f8c13b67d	api	2026-05-13 13:25:49.261053+00
08aa7f65-a076-4170-8a23-06f9864a1193	a8eea1e3-8697-4bdd-a735-e0d1222816d6	f7eaecff-8db0-466b-a734-74294dca8654	ledger.created	ledger	c2a20c2a-f943-47b7-9e2d-f552f5712fdc	null	{"id": "c2a20c2a-f943-47b7-9e2d-f552f5712fdc", "pan": null, "name": "Party-f677", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "a8eea1e3-8697-4bdd-a735-e0d1222816d6", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-f677", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	40ee6eb4-0ee2-4884-8689-1b70a5f20ff5	api	2026-05-13 13:25:49.292233+00
ca4219aa-458c-4c57-b0c5-e912295835ba	\N	abfdd8f5-32d5-4358-933a-c786fb875466	user.created	user	abfdd8f5-32d5-4358-933a-c786fb875466	null	{"id": "abfdd8f5-32d5-4358-933a-c786fb875466", "email": "v7user-db18bf9530@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	c3984856-f619-466f-a028-1e12c529c1fc	api	2026-05-13 13:25:51.710785+00
af362cae-df85-443e-b102-cb1d9703257f	73c6c8f6-5d56-473a-92cc-88e036e6f515	abfdd8f5-32d5-4358-933a-c786fb875466	company.created	company	73c6c8f6-5d56-473a-92cc-88e036e6f515	null	{"id": "73c6c8f6-5d56-473a-92cc-88e036e6f515", "pan": null, "city": null, "name": "Acme-b0c531", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	f599f2ec-2151-46d0-9dcf-252407e8441e	api	2026-05-13 13:25:52.073136+00
606cac42-f7c3-4ec6-95c7-b4aa2f91cba3	73c6c8f6-5d56-473a-92cc-88e036e6f515	abfdd8f5-32d5-4358-933a-c786fb875466	ledger.created	ledger	cab8fd0a-8c4d-4364-b024-fabec883e579	null	{"id": "cab8fd0a-8c4d-4364-b024-fabec883e579", "pan": null, "name": "Party-e5ad", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "73c6c8f6-5d56-473a-92cc-88e036e6f515", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-e5ad", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	18f91ed6-76d6-459e-8ff9-8ceb96310bcc	api	2026-05-13 13:25:52.103203+00
7fda8508-834b-4023-ad6c-c9b336d2a6c0	\N	df5b8c8b-2083-40fb-8815-e3b289b88f81	user.created	user	df5b8c8b-2083-40fb-8815-e3b289b88f81	null	{"id": "df5b8c8b-2083-40fb-8815-e3b289b88f81", "email": "v7user-49da1e7711@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	92fb1f3a-fe92-4b16-9de8-3ed6d11fb191	api	2026-05-13 13:25:54.71153+00
526cad14-92fe-41fb-91c9-3a8b8162e471	d73af30c-8808-471d-83ca-553d0163247f	df5b8c8b-2083-40fb-8815-e3b289b88f81	company.created	company	d73af30c-8808-471d-83ca-553d0163247f	null	{"id": "d73af30c-8808-471d-83ca-553d0163247f", "pan": null, "city": null, "name": "Acme-29c170", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	80a7238d-f28d-4010-9e9d-427d1098f445	api	2026-05-13 13:25:55.073358+00
4db986a5-9fb1-40e8-9855-949e9d24ee8f	d73af30c-8808-471d-83ca-553d0163247f	df5b8c8b-2083-40fb-8815-e3b289b88f81	ledger.created	ledger	6026bc11-9a1b-49bb-8bec-70174bf5138b	null	{"id": "6026bc11-9a1b-49bb-8bec-70174bf5138b", "pan": null, "name": "Party-629a", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "d73af30c-8808-471d-83ca-553d0163247f", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-629a", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	5bf6f289-49a7-4339-8c3c-b595ffee1de0	api	2026-05-13 13:25:55.098663+00
c1ad8116-a605-4340-a7c0-f78c770f25cd	\N	19b0ceeb-b97f-4ba4-9d13-6017f516d171	user.created	user	19b0ceeb-b97f-4ba4-9d13-6017f516d171	null	{"id": "19b0ceeb-b97f-4ba4-9d13-6017f516d171", "email": "v7user-7f24290c0f@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	044d9739-c81a-42bd-a11e-fdc823a272d0	api	2026-05-13 13:25:55.108423+00
ef52a88e-1a12-40d7-879b-a912ba270a43	b6b8b865-f868-485b-9a5d-74964a413d45	19b0ceeb-b97f-4ba4-9d13-6017f516d171	company.created	company	b6b8b865-f868-485b-9a5d-74964a413d45	null	{"id": "b6b8b865-f868-485b-9a5d-74964a413d45", "pan": null, "city": null, "name": "Acme-ebe9de", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	e39c3855-ea46-4391-a3a7-346b92814945	api	2026-05-13 13:25:55.466148+00
a0ac0c34-fada-47c6-be6e-f518486f2af1	2b0bea11-d862-401f-b128-ec6e48cec60d	5f0759b7-260a-4a7a-a61e-c113939f9206	ledger.created	ledger	c42a75a0-77bc-4eb7-950a-ef322a6713a2	null	{"id": "c42a75a0-77bc-4eb7-950a-ef322a6713a2", "pan": null, "name": "Bank-96a2", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "2b0bea11-d862-401f-b128-ec6e48cec60d", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-96a2", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	d700fc93-68cf-43d8-a92e-6130972a28e9	api	2026-05-13 13:25:58.103885+00
61a71eb3-29bf-49b7-8508-7f11398394e1	2b0bea11-d862-401f-b128-ec6e48cec60d	5f0759b7-260a-4a7a-a61e-c113939f9206	voucher.created	voucher	2afeee51-7b03-4d45-a4ef-54917c6d3964	null	{"id": "2afeee51-7b03-4d45-a4ef-54917c6d3964", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "c42a75a0-77bc-4eb7-950a-ef322a6713a2", "narration": null, "entry_type": "Dr", "line_number": 1}, {"amount": "1500.99", "ledger_id": "df3d3586-7f3f-4cba-9628-705650a69902", "narration": null, "entry_type": "Cr", "line_number": 2}], "narration": "Validation suite test voucher", "reference": null, "company_id": "2b0bea11-d862-401f-b128-ec6e48cec60d", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	90961059-a434-46cb-a2d8-b8300edc264c	api	2026-05-13 13:25:58.539514+00
28f9099e-fa7d-4383-ab1b-35d84912ae8b	\N	d072f2df-465f-4aaf-bbbf-663ccbe52dbe	user.created	user	d072f2df-465f-4aaf-bbbf-663ccbe52dbe	null	{"id": "d072f2df-465f-4aaf-bbbf-663ccbe52dbe", "email": "v7user-4a83f867a2@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	008f2d8b-4064-4228-9db2-ec9ec44aeb0d	api	2026-05-13 13:26:00.736176+00
411849ab-b145-49e7-a380-37ae68527dee	60ca15f3-0e5c-432b-8b0d-a77d783129b3	d072f2df-465f-4aaf-bbbf-663ccbe52dbe	company.created	company	60ca15f3-0e5c-432b-8b0d-a77d783129b3	null	{"id": "60ca15f3-0e5c-432b-8b0d-a77d783129b3", "pan": null, "city": null, "name": "Acme-f9a530", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	c3e81112-6223-40a4-b041-d3e11b78c5d0	api	2026-05-13 13:26:01.096768+00
ae6a0f2b-f07d-4a9c-8b3f-70fd867e8504	60ca15f3-0e5c-432b-8b0d-a77d783129b3	d072f2df-465f-4aaf-bbbf-663ccbe52dbe	ledger.created	ledger	80f8f35e-aeb5-4ae9-9c4c-5c904e1df46f	null	{"id": "80f8f35e-aeb5-4ae9-9c4c-5c904e1df46f", "pan": null, "name": "Party-0aae", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "60ca15f3-0e5c-432b-8b0d-a77d783129b3", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-0aae", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	5d99cb22-db21-4c80-8722-8eb9a99c472a	api	2026-05-13 13:26:01.126741+00
cd8ba398-bf8c-4ea8-8550-66f18cb6f36d	a8eea1e3-8697-4bdd-a735-e0d1222816d6	f7eaecff-8db0-466b-a734-74294dca8654	ledger.created	ledger	30f7c49b-7ced-4625-a59f-2714d18eca80	null	{"id": "30f7c49b-7ced-4625-a59f-2714d18eca80", "pan": null, "name": "Bank-89fd", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "a8eea1e3-8697-4bdd-a735-e0d1222816d6", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-89fd", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	ff61b009-1d7d-4d2c-b218-68102b2777fc	api	2026-05-13 13:25:49.277631+00
6ff703ee-722c-493b-a9b4-4bbc77f6165c	a8eea1e3-8697-4bdd-a735-e0d1222816d6	f7eaecff-8db0-466b-a734-74294dca8654	voucher.created	voucher	2e5f9e23-b025-47a9-8007-efba746dd2dd	null	{"id": "2e5f9e23-b025-47a9-8007-efba746dd2dd", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "30f7c49b-7ced-4625-a59f-2714d18eca80", "narration": null, "entry_type": "Dr", "line_number": 1}, {"amount": "1500.99", "ledger_id": "c2a20c2a-f943-47b7-9e2d-f552f5712fdc", "narration": null, "entry_type": "Cr", "line_number": 2}], "narration": "Validation suite test voucher", "reference": null, "company_id": "a8eea1e3-8697-4bdd-a735-e0d1222816d6", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	0fc56fd4-9b6d-4e67-b7f7-74d7cbcf722f	api	2026-05-13 13:25:49.396815+00
c9266773-281d-4ce4-8379-245498b6cbab	73c6c8f6-5d56-473a-92cc-88e036e6f515	abfdd8f5-32d5-4358-933a-c786fb875466	ledger.created	ledger	0ba833dd-2220-4be7-82d3-ff2dc592ca55	null	{"id": "0ba833dd-2220-4be7-82d3-ff2dc592ca55", "pan": null, "name": "Bank-3fdd", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "73c6c8f6-5d56-473a-92cc-88e036e6f515", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-3fdd", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	478fe510-3401-4521-b935-4b07c3b05684	api	2026-05-13 13:25:52.087405+00
ad013a92-e34b-466a-9347-55c390900f5d	73c6c8f6-5d56-473a-92cc-88e036e6f515	abfdd8f5-32d5-4358-933a-c786fb875466	voucher.created	voucher	4db81436-b985-49f7-97b3-17e3e3a19022	null	{"id": "4db81436-b985-49f7-97b3-17e3e3a19022", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "0ba833dd-2220-4be7-82d3-ff2dc592ca55", "narration": null, "entry_type": "Dr", "line_number": 1}, {"amount": "1500.99", "ledger_id": "cab8fd0a-8c4d-4364-b024-fabec883e579", "narration": null, "entry_type": "Cr", "line_number": 2}], "narration": "Validation suite test voucher", "reference": null, "company_id": "73c6c8f6-5d56-473a-92cc-88e036e6f515", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	7df653b0-43b7-4b05-8238-9b2cc0bdafb2	api	2026-05-13 13:25:52.117945+00
3cf012b9-626b-4ef3-b0b9-21ba15b3d3f3	\N	08d3d460-f4d5-45c2-96c5-7d2f4e85e2b5	user.created	user	08d3d460-f4d5-45c2-96c5-7d2f4e85e2b5	null	{"id": "08d3d460-f4d5-45c2-96c5-7d2f4e85e2b5", "email": "v7user-265fcbf3f9@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	f6f34e8e-c536-4f11-96d5-84db6014829c	api	2026-05-13 13:25:52.137662+00
31ed45d1-7d90-4b9b-8227-d403a5f94da7	908c5157-d04e-40e6-b01e-e3ba44912beb	08d3d460-f4d5-45c2-96c5-7d2f4e85e2b5	company.created	company	908c5157-d04e-40e6-b01e-e3ba44912beb	null	{"id": "908c5157-d04e-40e6-b01e-e3ba44912beb", "pan": null, "city": null, "name": "Acme-608dff", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	a36649eb-78e7-4aa6-9ca3-d08e66836fdc	api	2026-05-13 13:25:52.505195+00
28a728e0-743b-4d72-950d-9c7a391229b3	d73af30c-8808-471d-83ca-553d0163247f	df5b8c8b-2083-40fb-8815-e3b289b88f81	ledger.created	ledger	1d4febcb-3c9f-4069-a36e-02996d5b1e19	null	{"id": "1d4febcb-3c9f-4069-a36e-02996d5b1e19", "pan": null, "name": "Bank-bbce", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "d73af30c-8808-471d-83ca-553d0163247f", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-bbce", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	5c24adae-b79b-438f-8a09-3901f6aba8e6	api	2026-05-13 13:25:55.086816+00
e834dda4-816c-4d47-a186-59b8b249ca0d	\N	5f0759b7-260a-4a7a-a61e-c113939f9206	user.created	user	5f0759b7-260a-4a7a-a61e-c113939f9206	null	{"id": "5f0759b7-260a-4a7a-a61e-c113939f9206", "email": "v7user-76a8a81659@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	085430c9-19b2-4b72-b70c-2d8469f06385	api	2026-05-13 13:25:57.683238+00
e2864c80-b236-4640-b8e9-e78f24522bdb	2b0bea11-d862-401f-b128-ec6e48cec60d	5f0759b7-260a-4a7a-a61e-c113939f9206	company.created	company	2b0bea11-d862-401f-b128-ec6e48cec60d	null	{"id": "2b0bea11-d862-401f-b128-ec6e48cec60d", "pan": null, "city": null, "name": "Acme-a5f508", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	97cc6e6c-c074-4023-8c4b-c7dd3e08aac5	api	2026-05-13 13:25:58.061536+00
5ece6cdd-6328-456b-bfed-9701a74cb1f6	2b0bea11-d862-401f-b128-ec6e48cec60d	5f0759b7-260a-4a7a-a61e-c113939f9206	ledger.created	ledger	df3d3586-7f3f-4cba-9628-705650a69902	null	{"id": "df3d3586-7f3f-4cba-9628-705650a69902", "pan": null, "name": "Party-d012", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "2b0bea11-d862-401f-b128-ec6e48cec60d", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-d012", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	d9163552-01e9-4407-8b96-489ec01039de	api	2026-05-13 13:25:58.123523+00
e398a909-c2c2-4cdd-b28a-799f59a26c45	\N	a60dfcad-bf58-49c4-9435-fc9b1de6523c	user.created	user	a60dfcad-bf58-49c4-9435-fc9b1de6523c	null	{"id": "a60dfcad-bf58-49c4-9435-fc9b1de6523c", "email": "v7user-4cdce9faff@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	f8f7aa26-1cc8-405d-93a3-0fae56a72eba	api	2026-05-13 13:25:58.135276+00
956ce1af-859b-414d-8a10-591c8468b1d9	ebbf6003-37d5-4265-9184-8fdfaba5793a	a60dfcad-bf58-49c4-9435-fc9b1de6523c	company.created	company	ebbf6003-37d5-4265-9184-8fdfaba5793a	null	{"id": "ebbf6003-37d5-4265-9184-8fdfaba5793a", "pan": null, "city": null, "name": "Acme-1c1240", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	2be5f176-e732-4558-a178-f405956e085b	api	2026-05-13 13:25:58.524947+00
b4d7df56-ad8a-4f3a-91a7-ed008e83566b	60ca15f3-0e5c-432b-8b0d-a77d783129b3	d072f2df-465f-4aaf-bbbf-663ccbe52dbe	ledger.created	ledger	61be9fec-5338-485e-b524-57a768a54cde	null	{"id": "61be9fec-5338-485e-b524-57a768a54cde", "pan": null, "name": "Bank-6704", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "60ca15f3-0e5c-432b-8b0d-a77d783129b3", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-6704", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	3ea54187-8322-4847-a291-f240be2ea0fe	api	2026-05-13 13:26:01.114493+00
b9363155-8e45-4470-a576-c7fd0f528e17	\N	8d40beaf-87e9-47f6-8f8f-68e350011e76	user.created	user	8d40beaf-87e9-47f6-8f8f-68e350011e76	null	{"id": "8d40beaf-87e9-47f6-8f8f-68e350011e76", "email": "v7user-05c16b068f@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	256aa6dc-f8b3-4652-add8-9c3bd2d502bb	api	2026-05-13 13:26:03.351646+00
f83987ca-c1a1-4813-afee-746480770377	28819b28-ffd6-4877-b097-d68542903509	8d40beaf-87e9-47f6-8f8f-68e350011e76	company.created	company	28819b28-ffd6-4877-b097-d68542903509	null	{"id": "28819b28-ffd6-4877-b097-d68542903509", "pan": null, "city": null, "name": "Acme-80ec7a", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	bf3c0878-c56d-4e26-9d0b-67034169dc33	api	2026-05-13 13:26:03.708057+00
20c60e87-f672-4bbf-ae4c-64909351f157	28819b28-ffd6-4877-b097-d68542903509	8d40beaf-87e9-47f6-8f8f-68e350011e76	ledger.created	ledger	9a71b5a6-fcae-40ff-ad15-5989c8fc160f	null	{"id": "9a71b5a6-fcae-40ff-ad15-5989c8fc160f", "pan": null, "name": "Party-6129", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "28819b28-ffd6-4877-b097-d68542903509", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-6129", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	2240a87a-4d82-4eee-933e-668212e80659	api	2026-05-13 13:26:03.743895+00
b9c3c51f-2806-4d10-9af0-c589e3e257c5	fde328ce-64f3-4555-b472-d0a0f0ab7313	e53e23ba-5809-4965-a6e2-129f3c7682f1	ledger.created	ledger	4163e6de-d7ad-42c6-8156-f41807a72e9c	null	{"id": "4163e6de-d7ad-42c6-8156-f41807a72e9c", "pan": null, "name": "Bank-B-dd7b", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "fde328ce-64f3-4555-b472-d0a0f0ab7313", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-b-dd7b", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	02603173-6866-48b5-b785-7c09087f4771	api	2026-05-13 13:26:04.151521+00
d6bf8296-9d92-4c0a-b508-5b1ed52a64c1	fde328ce-64f3-4555-b472-d0a0f0ab7313	e53e23ba-5809-4965-a6e2-129f3c7682f1	voucher.created	voucher	0f5b7e2a-6032-4765-ad1f-355b0c01919e	null	{"id": "0f5b7e2a-6032-4765-ad1f-355b0c01919e", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "4163e6de-d7ad-42c6-8156-f41807a72e9c", "narration": null, "entry_type": "Dr", "line_number": 1}, {"amount": "1500.99", "ledger_id": "c308bfc6-2bca-4bc9-ba96-0abaf2606a1e", "narration": null, "entry_type": "Cr", "line_number": 2}], "narration": "company-B row", "reference": null, "company_id": "fde328ce-64f3-4555-b472-d0a0f0ab7313", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	cc724ad1-300e-49a4-95cf-ea706c9836be	api	2026-05-13 13:26:04.181062+00
ec0d56d2-e956-4ee9-ac13-85665772cf21	c9701980-a203-4995-81db-50eb351db7cd	781417d6-2f31-4388-a3d1-19ea97d14754	ledger.created	ledger	42171f00-a457-4b71-9f44-710030c9b921	null	{"id": "42171f00-a457-4b71-9f44-710030c9b921", "pan": null, "name": "Bank-0ad6", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "c9701980-a203-4995-81db-50eb351db7cd", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-0ad6", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	448ebbf6-bcf9-41c0-a720-919d50eea8f4	api	2026-05-13 13:26:06.788361+00
bf3e2dcb-fdeb-41b4-b4c7-545e54f4c093	c9701980-a203-4995-81db-50eb351db7cd	781417d6-2f31-4388-a3d1-19ea97d14754	voucher.created	voucher	2f7c9ed9-f337-413b-8119-5cd04343c1a1	null	{"id": "2f7c9ed9-f337-413b-8119-5cd04343c1a1", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "42171f00-a457-4b71-9f44-710030c9b921", "narration": null, "entry_type": "Dr", "line_number": 1}, {"amount": "1500.99", "ledger_id": "fa4d102a-ef67-4ce4-8922-58c6bf860a41", "narration": null, "entry_type": "Cr", "line_number": 2}], "narration": "Validation suite test voucher", "reference": null, "company_id": "c9701980-a203-4995-81db-50eb351db7cd", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	962875e2-f9f1-44ce-bcea-7a7d448c8868	api	2026-05-13 13:26:06.814545+00
a4c0df94-8816-45ae-93df-b5f0dcbbd399	7ddbbff3-244f-42c3-b142-530b02e7f739	beccb035-b15f-4e8f-98dc-bba5381522c2	ledger.created	ledger	adf6af3c-c451-4faf-9c68-b212aee4eaf8	null	{"id": "adf6af3c-c451-4faf-9c68-b212aee4eaf8", "pan": null, "name": "Bank-71d8", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "7ddbbff3-244f-42c3-b142-530b02e7f739", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-71d8", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	207a7564-5487-4ebe-a7f4-7773f13dfba7	api	2026-05-13 13:26:09.546055+00
d42e9fca-c60a-4bf3-b9ee-339667adcbfa	7ddbbff3-244f-42c3-b142-530b02e7f739	beccb035-b15f-4e8f-98dc-bba5381522c2	voucher.created	voucher	c5a3602f-5caa-4010-9354-162303de746d	null	{"id": "c5a3602f-5caa-4010-9354-162303de746d", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "71c0a0a6-afcf-48c2-ae41-ea4c1dc8c70e", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "1500.99", "ledger_id": "adf6af3c-c451-4faf-9c68-b212aee4eaf8", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "original narration", "reference": null, "company_id": "7ddbbff3-244f-42c3-b142-530b02e7f739", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	924b78e0-edb9-406c-b95a-c311a4957e59	api	2026-05-13 13:26:09.585838+00
28ab78b2-a07b-46e6-9950-9a7680d91571	28819b28-ffd6-4877-b097-d68542903509	8d40beaf-87e9-47f6-8f8f-68e350011e76	ledger.created	ledger	f4d38d67-5532-4f4e-b4be-01afc7b06bbe	null	{"id": "f4d38d67-5532-4f4e-b4be-01afc7b06bbe", "pan": null, "name": "Bank-1377", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "28819b28-ffd6-4877-b097-d68542903509", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-1377", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	cc8f037b-f4d9-4847-93eb-4b1e02323812	api	2026-05-13 13:26:03.728119+00
5ef9e065-21bc-43ad-beb3-ce7f4e8d4873	28819b28-ffd6-4877-b097-d68542903509	8d40beaf-87e9-47f6-8f8f-68e350011e76	voucher.created	voucher	1a2c939b-7efd-4e6f-8ff1-cb15504883c0	null	{"id": "1a2c939b-7efd-4e6f-8ff1-cb15504883c0", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "9a71b5a6-fcae-40ff-ad15-5989c8fc160f", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "1500.99", "ledger_id": "f4d38d67-5532-4f4e-b4be-01afc7b06bbe", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "company-A row", "reference": null, "company_id": "28819b28-ffd6-4877-b097-d68542903509", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	0c074f1f-250b-474f-a3e6-e9c09cf5416f	api	2026-05-13 13:26:03.756565+00
b7bb5236-2fe6-4b56-8d0b-4f4ea1ae7f1a	\N	e53e23ba-5809-4965-a6e2-129f3c7682f1	user.created	user	e53e23ba-5809-4965-a6e2-129f3c7682f1	null	{"id": "e53e23ba-5809-4965-a6e2-129f3c7682f1", "email": "v7user-e677de16f8@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	b2e988ae-1074-4376-b867-a1690ef724ac	api	2026-05-13 13:26:03.775135+00
2465df91-363d-4418-a065-dc10d52abea8	fde328ce-64f3-4555-b472-d0a0f0ab7313	e53e23ba-5809-4965-a6e2-129f3c7682f1	company.created	company	fde328ce-64f3-4555-b472-d0a0f0ab7313	null	{"id": "fde328ce-64f3-4555-b472-d0a0f0ab7313", "pan": null, "city": null, "name": "Acme-5d40f9", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	8c31475e-4ab9-417b-8b88-6fdaa4c29650	api	2026-05-13 13:26:04.133318+00
76cd9d1d-a0f2-412d-b565-5d67d11c8524	fde328ce-64f3-4555-b472-d0a0f0ab7313	e53e23ba-5809-4965-a6e2-129f3c7682f1	ledger.created	ledger	c308bfc6-2bca-4bc9-ba96-0abaf2606a1e	null	{"id": "c308bfc6-2bca-4bc9-ba96-0abaf2606a1e", "pan": null, "name": "Party-B-b7a3", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "fde328ce-64f3-4555-b472-d0a0f0ab7313", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-b-b7a3", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	9fa80728-ac9c-494f-bf5f-42a0dc38c8a8	api	2026-05-13 13:26:04.166585+00
36d545e9-cc0e-452c-9820-3973c6036522	\N	781417d6-2f31-4388-a3d1-19ea97d14754	user.created	user	781417d6-2f31-4388-a3d1-19ea97d14754	null	{"id": "781417d6-2f31-4388-a3d1-19ea97d14754", "email": "v7user-58c9e830c7@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	96d8f538-4414-47c9-b8b1-a99bd07bdbe5	api	2026-05-13 13:26:06.414237+00
184ad4f0-ee90-4c0a-ad8d-a3a4b6ed73f4	c9701980-a203-4995-81db-50eb351db7cd	781417d6-2f31-4388-a3d1-19ea97d14754	company.created	company	c9701980-a203-4995-81db-50eb351db7cd	null	{"id": "c9701980-a203-4995-81db-50eb351db7cd", "pan": null, "city": null, "name": "Acme-7432cc", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	674b98e8-c720-4552-81d4-f56bf272b31f	api	2026-05-13 13:26:06.774564+00
8be85e16-c518-401c-8488-a3f4d82861b2	c9701980-a203-4995-81db-50eb351db7cd	781417d6-2f31-4388-a3d1-19ea97d14754	ledger.created	ledger	fa4d102a-ef67-4ce4-8922-58c6bf860a41	null	{"id": "fa4d102a-ef67-4ce4-8922-58c6bf860a41", "pan": null, "name": "Party-a02e", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "c9701980-a203-4995-81db-50eb351db7cd", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-a02e", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	e1e1936d-376e-4507-8ecd-cc2d7e44bdb5	api	2026-05-13 13:26:06.800682+00
6ee7d473-8be7-46fb-832e-000bb2e6f2f7	\N	beccb035-b15f-4e8f-98dc-bba5381522c2	user.created	user	beccb035-b15f-4e8f-98dc-bba5381522c2	null	{"id": "beccb035-b15f-4e8f-98dc-bba5381522c2", "email": "v7user-d9c7d58255@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	2616945e-55c9-4be5-93f1-2fb17d288ab8	api	2026-05-13 13:26:09.170645+00
ea59a1d0-2092-4c13-88f1-c20ce8c78da9	7ddbbff3-244f-42c3-b142-530b02e7f739	beccb035-b15f-4e8f-98dc-bba5381522c2	company.created	company	7ddbbff3-244f-42c3-b142-530b02e7f739	null	{"id": "7ddbbff3-244f-42c3-b142-530b02e7f739", "pan": null, "city": null, "name": "Acme-ef7434", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	0b48c867-e07c-4bfe-8e79-012943bbc368	api	2026-05-13 13:26:09.52884+00
a1e326cf-8c3e-4740-9c93-7206942f061d	7ddbbff3-244f-42c3-b142-530b02e7f739	beccb035-b15f-4e8f-98dc-bba5381522c2	ledger.created	ledger	71c0a0a6-afcf-48c2-ae41-ea4c1dc8c70e	null	{"id": "71c0a0a6-afcf-48c2-ae41-ea4c1dc8c70e", "pan": null, "name": "Party-a4c4", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "7ddbbff3-244f-42c3-b142-530b02e7f739", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-a4c4", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	9360fe7e-5101-42dd-8a95-5545ecf6c145	api	2026-05-13 13:26:09.56803+00
22bcd31b-24b7-48a1-82ac-a53da2ed126c	7ddbbff3-244f-42c3-b142-530b02e7f739	beccb035-b15f-4e8f-98dc-bba5381522c2	voucher.updated	voucher	c5a3602f-5caa-4010-9354-162303de746d	{"id": "c5a3602f-5caa-4010-9354-162303de746d", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "71c0a0a6-afcf-48c2-ae41-ea4c1dc8c70e", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "1500.99", "ledger_id": "adf6af3c-c451-4faf-9c68-b212aee4eaf8", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "original narration", "reference": null, "company_id": "7ddbbff3-244f-42c3-b142-530b02e7f739", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{"id": "c5a3602f-5caa-4010-9354-162303de746d", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "71c0a0a6-afcf-48c2-ae41-ea4c1dc8c70e", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "1500.99", "ledger_id": "adf6af3c-c451-4faf-9c68-b212aee4eaf8", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "updated narration", "reference": null, "company_id": "7ddbbff3-244f-42c3-b142-530b02e7f739", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{"narration": ["original narration", "updated narration"]}	127.0.0.1	python-httpx/0.28.1	aa44bae4-3916-437c-b9df-71392bcf3d23	api	2026-05-13 13:26:09.61037+00
df4c2fcf-048d-4d55-a4f8-9c1aaecd75f8	\N	00b34604-0adb-4950-b38c-25b0d81424f6	user.created	user	00b34604-0adb-4950-b38c-25b0d81424f6	null	{"id": "00b34604-0adb-4950-b38c-25b0d81424f6", "email": "v7user-f0b6eb39a4@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	3db27c61-0b46-4355-8264-763d75992526	api	2026-05-13 13:26:11.857965+00
7acc47fb-957d-45f9-af92-660d1f7377e2	09214117-1d79-4d43-b074-028661f423bd	00b34604-0adb-4950-b38c-25b0d81424f6	company.created	company	09214117-1d79-4d43-b074-028661f423bd	null	{"id": "09214117-1d79-4d43-b074-028661f423bd", "pan": null, "city": null, "name": "Acme-5e3133", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	dca1bc89-a6cb-4c1a-b117-275d4abfeda5	api	2026-05-13 13:26:12.229119+00
69590b95-6579-49b1-a06f-81b2ccf0e5e4	09214117-1d79-4d43-b074-028661f423bd	00b34604-0adb-4950-b38c-25b0d81424f6	ledger.created	ledger	704fe26c-4c5f-446b-80ef-9d959252c61a	null	{"id": "704fe26c-4c5f-446b-80ef-9d959252c61a", "pan": null, "name": "Bank-9c54", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "09214117-1d79-4d43-b074-028661f423bd", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-9c54", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	14e500a8-7574-4a3b-87cb-e1d53b286bae	api	2026-05-13 13:26:12.244296+00
7af724ae-1aec-4698-95b1-02606cea8ef3	09214117-1d79-4d43-b074-028661f423bd	00b34604-0adb-4950-b38c-25b0d81424f6	ledger.created	ledger	0e3c1f4e-04ce-42a3-8c68-6461f31d0727	null	{"id": "0e3c1f4e-04ce-42a3-8c68-6461f31d0727", "pan": null, "name": "Party-3dc1", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "09214117-1d79-4d43-b074-028661f423bd", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-3dc1", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	dd00997a-6dd2-4b60-8aeb-1c4379a9f4da	api	2026-05-13 13:26:12.258175+00
1990d360-6ab8-408f-96a3-e35c83f80889	09214117-1d79-4d43-b074-028661f423bd	00b34604-0adb-4950-b38c-25b0d81424f6	voucher.created	voucher	4c09ff5c-fc40-430e-900c-fdf3733e808b	null	{"id": "4c09ff5c-fc40-430e-900c-fdf3733e808b", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "0e3c1f4e-04ce-42a3-8c68-6461f31d0727", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "1500.99", "ledger_id": "704fe26c-4c5f-446b-80ef-9d959252c61a", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "Validation suite test voucher", "reference": null, "company_id": "09214117-1d79-4d43-b074-028661f423bd", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	04e152a3-8124-4289-a8ba-af3e49cb1020	api	2026-05-13 13:26:12.272876+00
5a687490-10be-46a2-806b-911d735439e9	09214117-1d79-4d43-b074-028661f423bd	00b34604-0adb-4950-b38c-25b0d81424f6	voucher.updated	voucher	4c09ff5c-fc40-430e-900c-fdf3733e808b	{"id": "4c09ff5c-fc40-430e-900c-fdf3733e808b", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "0e3c1f4e-04ce-42a3-8c68-6461f31d0727", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "1500.99", "ledger_id": "704fe26c-4c5f-446b-80ef-9d959252c61a", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "Validation suite test voucher", "reference": null, "company_id": "09214117-1d79-4d43-b074-028661f423bd", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{"id": "4c09ff5c-fc40-430e-900c-fdf3733e808b", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "0e3c1f4e-04ce-42a3-8c68-6461f31d0727", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "1500.99", "ledger_id": "704fe26c-4c5f-446b-80ef-9d959252c61a", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "pre-cancel update", "reference": null, "company_id": "09214117-1d79-4d43-b074-028661f423bd", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{"narration": ["Validation suite test voucher", "pre-cancel update"]}	127.0.0.1	python-httpx/0.28.1	407b406d-756c-415a-b0b0-93a8553548bc	api	2026-05-13 13:26:12.29855+00
1dcc5b0b-7ff7-496e-a697-9b61c3944077	09214117-1d79-4d43-b074-028661f423bd	00b34604-0adb-4950-b38c-25b0d81424f6	voucher.cancelled	voucher	4c09ff5c-fc40-430e-900c-fdf3733e808b	{"id": "4c09ff5c-fc40-430e-900c-fdf3733e808b", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "0e3c1f4e-04ce-42a3-8c68-6461f31d0727", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "1500.99", "ledger_id": "704fe26c-4c5f-446b-80ef-9d959252c61a", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "pre-cancel update", "reference": null, "company_id": "09214117-1d79-4d43-b074-028661f423bd", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{"id": "4c09ff5c-fc40-430e-900c-fdf3733e808b", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "cancelled", "entries": [{"amount": "1500.99", "ledger_id": "0e3c1f4e-04ce-42a3-8c68-6461f31d0727", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "1500.99", "ledger_id": "704fe26c-4c5f-446b-80ef-9d959252c61a", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "pre-cancel update", "reference": null, "company_id": "09214117-1d79-4d43-b074-028661f423bd", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "cancel_reason": "validation suite teardown", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{"status": ["posted", "cancelled"], "cancel_reason": [null, "validation suite teardown"]}	127.0.0.1	python-httpx/0.28.1	de9fb4dd-9202-4dae-a0cf-970261aec815	api	2026-05-13 13:26:12.317011+00
5f850ad0-c96a-4e41-b24b-333fa0f0f38b	\N	0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2	user.created	user	0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2	null	{"id": "0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2", "email": "v7user-10d9a3aa2e@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	367b154a-f284-406e-97eb-8df0e9ffc110	api	2026-05-13 13:26:14.553507+00
77dbe440-c4da-4171-bf45-6ce837ea958f	8ad00365-70bb-40bb-8f0c-e735597dcf86	0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2	company.created	company	8ad00365-70bb-40bb-8f0c-e735597dcf86	null	{"id": "8ad00365-70bb-40bb-8f0c-e735597dcf86", "pan": null, "city": null, "name": "Acme-a30660", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	fbc6bdcd-b41e-4cd2-af38-6d007c6d0071	api	2026-05-13 13:26:14.914642+00
444a5a93-ca6c-44cf-8f6d-021779d09698	8ad00365-70bb-40bb-8f0c-e735597dcf86	0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2	ledger.created	ledger	3f38cd90-4c4b-4da7-9f4a-3294616f8704	null	{"id": "3f38cd90-4c4b-4da7-9f4a-3294616f8704", "pan": null, "name": "Bank-e280", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "8ad00365-70bb-40bb-8f0c-e735597dcf86", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-e280", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	e34fef89-309d-435b-b205-0e624da4c858	api	2026-05-13 13:26:14.931873+00
79b47c79-613b-4462-810e-d48adb8d2849	8ad00365-70bb-40bb-8f0c-e735597dcf86	0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2	voucher.created	voucher	10f24d67-945f-43e8-aca1-5ceed49397f5	null	{"id": "10f24d67-945f-43e8-aca1-5ceed49397f5", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "332aa823-d64d-48fb-b908-4f4768621731", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "1500.99", "ledger_id": "3f38cd90-4c4b-4da7-9f4a-3294616f8704", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "Validation suite test voucher", "reference": null, "company_id": "8ad00365-70bb-40bb-8f0c-e735597dcf86", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	64c4a4f6-2b89-422e-a120-90c8fcee40f1	api	2026-05-13 13:26:14.978365+00
cbc5965a-e71b-48fe-b656-ad83006bc446	9f6ced8d-775b-4061-bde5-7098fc05112a	aaddd93f-258f-46d1-8e79-6c91ca0c6e6f	ledger.created	ledger	61bf2ce0-a293-45ae-9d8f-5a84230a8457	null	{"id": "61bf2ce0-a293-45ae-9d8f-5a84230a8457", "pan": null, "name": "Bank-bd37", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "9f6ced8d-775b-4061-bde5-7098fc05112a", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-bd37", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	3ea3d79c-4f3b-4556-8685-cd647dac8891	api	2026-05-13 13:26:17.609991+00
fb17b6fd-6eb8-46f0-90b5-779a9397a621	\N	e5e8c459-79f3-4ac7-b2db-cee90c388ba8	user.created	user	e5e8c459-79f3-4ac7-b2db-cee90c388ba8	null	{"id": "e5e8c459-79f3-4ac7-b2db-cee90c388ba8", "email": "v7user-d97ad9f9b7@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	ca7fd9e7-7b8b-497c-a94a-8b12fc5c46f8	api	2026-05-13 13:26:20.220212+00
26106657-eaa7-4d92-9fca-8ae79d384281	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	e5e8c459-79f3-4ac7-b2db-cee90c388ba8	company.created	company	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	null	{"id": "6f1e7c99-48ec-42ec-8a78-111a5dd0f0af", "pan": null, "city": null, "name": "Acme-e50dd3", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	ea397635-c0cb-46a7-93e9-5542e989800b	api	2026-05-13 13:26:20.579889+00
2ec3b849-281c-42fb-894e-0c53af80486a	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	e5e8c459-79f3-4ac7-b2db-cee90c388ba8	ledger.created	ledger	86d3c0f6-f61d-4c8b-bcef-fa12c710a83d	null	{"id": "86d3c0f6-f61d-4c8b-bcef-fa12c710a83d", "pan": null, "name": "Party-ce7d", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "6f1e7c99-48ec-42ec-8a78-111a5dd0f0af", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-ce7d", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	5b0e4f22-2cb6-4f4a-9930-2a066761bf90	api	2026-05-13 13:26:20.608336+00
41777e46-4fe7-40a7-b24e-8ec7830219d6	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	e5e8c459-79f3-4ac7-b2db-cee90c388ba8	voucher.updated	voucher	374079a3-0845-4488-aeec-439ce8e056fc	{"id": "374079a3-0845-4488-aeec-439ce8e056fc", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "84142669-e981-4380-ad1e-01ea3792d93a", "narration": null, "entry_type": "Dr", "line_number": 1}, {"amount": "1500.99", "ledger_id": "86d3c0f6-f61d-4c8b-bcef-fa12c710a83d", "narration": null, "entry_type": "Cr", "line_number": 2}], "narration": "Validation suite test voucher", "reference": null, "company_id": "6f1e7c99-48ec-42ec-8a78-111a5dd0f0af", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{"id": "374079a3-0845-4488-aeec-439ce8e056fc", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "84142669-e981-4380-ad1e-01ea3792d93a", "narration": null, "entry_type": "Dr", "line_number": 1}, {"amount": "1500.99", "ledger_id": "86d3c0f6-f61d-4c8b-bcef-fa12c710a83d", "narration": null, "entry_type": "Cr", "line_number": 2}], "narration": "redaction-probe-606614", "reference": null, "company_id": "6f1e7c99-48ec-42ec-8a78-111a5dd0f0af", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{"narration": ["Validation suite test voucher", "redaction-probe-606614"]}	127.0.0.1	python-httpx/0.28.1	660c5cb1-314a-4f8a-bf37-0a1fb3ad63a1	api	2026-05-13 13:26:20.644889+00
df95a4fc-e877-421c-9343-af13e10dda09	249c8bde-f26e-4e5f-8caa-e5b1f476c86d	1b75c626-594e-4a15-87a3-0e6ad41958d1	ledger.created	ledger	9ea79806-0a6f-4501-9db8-78df81083bfe	null	{"id": "9ea79806-0a6f-4501-9db8-78df81083bfe", "pan": null, "name": "Bank-4422", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "249c8bde-f26e-4e5f-8caa-e5b1f476c86d", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-4422", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	0faa8f1e-4280-4aa1-88cd-8b0bc3c8db14	api	2026-05-13 13:26:23.36558+00
bbf2f6d8-0404-4b7a-95d4-d44eb3944bd7	249c8bde-f26e-4e5f-8caa-e5b1f476c86d	1b75c626-594e-4a15-87a3-0e6ad41958d1	voucher.created	voucher	aa00e22a-bd24-4105-a89e-e51723c0e0bf	null	{"id": "aa00e22a-bd24-4105-a89e-e51723c0e0bf", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "777.00", "ledger_id": "9ea79806-0a6f-4501-9db8-78df81083bfe", "narration": null, "entry_type": "Dr", "line_number": 1}, {"amount": "777.00", "ledger_id": "a8773b36-b3b6-498a-ada6-6abd30726546", "narration": null, "entry_type": "Cr", "line_number": 2}], "narration": "Validation suite test voucher", "reference": null, "company_id": "249c8bde-f26e-4e5f-8caa-e5b1f476c86d", "tds_amount": "0.00", "tds_section": null, "total_amount": "777.00", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	1fc2e1f3-39c6-484c-b02b-ff6b1d669959	api	2026-05-13 13:26:23.392231+00
3ba6c1c7-d0d9-4a1a-acbf-2599aa92a2e7	\N	956f8b68-30a0-42ee-9f8f-d4e52037c458	user.created	user	956f8b68-30a0-42ee-9f8f-d4e52037c458	null	{"id": "956f8b68-30a0-42ee-9f8f-d4e52037c458", "email": "v7user-175b47f10f@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	3c699a25-d8c9-4935-929d-ab46c33705bd	api	2026-05-13 13:26:25.626245+00
b2382f1e-7df8-4a1c-b464-a38243be8bb1	8ad00365-70bb-40bb-8f0c-e735597dcf86	0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2	ledger.created	ledger	332aa823-d64d-48fb-b908-4f4768621731	null	{"id": "332aa823-d64d-48fb-b908-4f4768621731", "pan": null, "name": "Party-5ca3", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "8ad00365-70bb-40bb-8f0c-e735597dcf86", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-5ca3", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	5e736f78-a361-4add-8518-378ab1adc04b	api	2026-05-13 13:26:14.948288+00
4c79d7bc-d82f-4a5e-b633-f6902b353910	\N	aaddd93f-258f-46d1-8e79-6c91ca0c6e6f	user.created	user	aaddd93f-258f-46d1-8e79-6c91ca0c6e6f	null	{"id": "aaddd93f-258f-46d1-8e79-6c91ca0c6e6f", "email": "v7user-5e2768fd14@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	cc3f7c32-1fc9-4ea1-8860-9ee019e564d3	api	2026-05-13 13:26:17.232321+00
af2bbb9b-3165-4219-857b-f1765e455e33	9f6ced8d-775b-4061-bde5-7098fc05112a	aaddd93f-258f-46d1-8e79-6c91ca0c6e6f	company.created	company	9f6ced8d-775b-4061-bde5-7098fc05112a	null	{"id": "9f6ced8d-775b-4061-bde5-7098fc05112a", "pan": null, "city": null, "name": "Acme-45f6e6", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	3e1a5b85-a189-4cff-9237-f3d1b537c834	api	2026-05-13 13:26:17.595625+00
ce6643f8-3054-46cc-8d96-a10471dba189	9f6ced8d-775b-4061-bde5-7098fc05112a	aaddd93f-258f-46d1-8e79-6c91ca0c6e6f	ledger.created	ledger	70dac9ed-76a0-4d29-a997-1bb475e461bb	null	{"id": "70dac9ed-76a0-4d29-a997-1bb475e461bb", "pan": null, "name": "Party-3415", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "9f6ced8d-775b-4061-bde5-7098fc05112a", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-3415", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	30dd8b76-3b00-462f-8d1b-a3af09b6e0cf	api	2026-05-13 13:26:17.622321+00
32993f1c-1a94-4ac6-98e3-c9dfe016db8c	\N	1e4f1313-810c-4b42-9be9-2ea4597a1a21	user.created	user	1e4f1313-810c-4b42-9be9-2ea4597a1a21	null	{"id": "1e4f1313-810c-4b42-9be9-2ea4597a1a21", "email": "v7user-70cd338ac9@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	891f03ad-3319-48b8-a071-b9b67f1fa662	api	2026-05-13 13:26:17.630197+00
f621a56e-bd28-4b80-9efb-c2f99011f725	9f6ced8d-775b-4061-bde5-7098fc05112a	aaddd93f-258f-46d1-8e79-6c91ca0c6e6f	user_company.role_assigned	user_company	dc9d6156-fab3-418d-af7e-a7b18adf9a4d	null	{"role": "viewer", "user_id": "1e4f1313-810c-4b42-9be9-2ea4597a1a21", "company_id": "9f6ced8d-775b-4061-bde5-7098fc05112a", "user_email": "v7user-70cd338ac9@example.com"}	{}	127.0.0.1	python-httpx/0.28.1	ce13f31f-82c0-44dd-b12f-be5601ca788a	api	2026-05-13 13:26:17.992492+00
d387f797-bcd8-4693-9907-3249a905f118	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	e5e8c459-79f3-4ac7-b2db-cee90c388ba8	ledger.created	ledger	84142669-e981-4380-ad1e-01ea3792d93a	null	{"id": "84142669-e981-4380-ad1e-01ea3792d93a", "pan": null, "name": "Bank-8795", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "6f1e7c99-48ec-42ec-8a78-111a5dd0f0af", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-8795", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	666af233-1673-402a-aa97-3e7fdc76a74b	api	2026-05-13 13:26:20.594603+00
e17b2b81-58cc-424d-90f7-c7643db0cd67	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	e5e8c459-79f3-4ac7-b2db-cee90c388ba8	voucher.created	voucher	374079a3-0845-4488-aeec-439ce8e056fc	null	{"id": "374079a3-0845-4488-aeec-439ce8e056fc", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1500.99", "ledger_id": "84142669-e981-4380-ad1e-01ea3792d93a", "narration": null, "entry_type": "Dr", "line_number": 1}, {"amount": "1500.99", "ledger_id": "86d3c0f6-f61d-4c8b-bcef-fa12c710a83d", "narration": null, "entry_type": "Cr", "line_number": 2}], "narration": "Validation suite test voucher", "reference": null, "company_id": "6f1e7c99-48ec-42ec-8a78-111a5dd0f0af", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	1f27a549-8d8b-481c-80a2-80d0e5872f68	api	2026-05-13 13:26:20.621447+00
971c4763-bd23-4d8f-b6d7-c4c56ed4cc04	\N	1b75c626-594e-4a15-87a3-0e6ad41958d1	user.created	user	1b75c626-594e-4a15-87a3-0e6ad41958d1	null	{"id": "1b75c626-594e-4a15-87a3-0e6ad41958d1", "email": "v7user-5cfad7384c@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	b11bdd38-844a-49bd-a30e-cb6181f1790a	api	2026-05-13 13:26:22.984523+00
3c8cb761-e1d2-4986-a19a-315e75c65108	249c8bde-f26e-4e5f-8caa-e5b1f476c86d	1b75c626-594e-4a15-87a3-0e6ad41958d1	company.created	company	249c8bde-f26e-4e5f-8caa-e5b1f476c86d	null	{"id": "249c8bde-f26e-4e5f-8caa-e5b1f476c86d", "pan": null, "city": null, "name": "Acme-dffd7b", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	c26cf900-0ac7-4472-8763-932551bb0564	api	2026-05-13 13:26:23.351868+00
2e6b2f57-c396-434a-8431-e4ff0beae96c	249c8bde-f26e-4e5f-8caa-e5b1f476c86d	1b75c626-594e-4a15-87a3-0e6ad41958d1	ledger.created	ledger	a8773b36-b3b6-498a-ada6-6abd30726546	null	{"id": "a8773b36-b3b6-498a-ada6-6abd30726546", "pan": null, "name": "Party-28d6", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "249c8bde-f26e-4e5f-8caa-e5b1f476c86d", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-28d6", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	c0d4aead-9bcb-438f-9f01-6fc64cd2a4ed	api	2026-05-13 13:26:23.377214+00
502cfecb-ce43-43dd-8f27-dfb04bc2f84f	b98ec1dc-23db-40d8-b9d6-e8e144da5f43	956f8b68-30a0-42ee-9f8f-d4e52037c458	ledger.created	ledger	98c9907a-c652-48f3-8806-b62320522340	null	{"id": "98c9907a-c652-48f3-8806-b62320522340", "pan": null, "name": "Bank-4f29", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "b98ec1dc-23db-40d8-b9d6-e8e144da5f43", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-4f29", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	9d28070e-a6ee-4359-b058-b6c8a720f0e7	api	2026-05-13 13:26:26.002636+00
c2f247fb-6121-49b3-b9ad-0e7ffbd90201	b98ec1dc-23db-40d8-b9d6-e8e144da5f43	956f8b68-30a0-42ee-9f8f-d4e52037c458	voucher.created	voucher	923a0f8b-974b-4f68-bf6f-f7ca16565910	null	{"id": "923a0f8b-974b-4f68-bf6f-f7ca16565910", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "777.00", "ledger_id": "3b200045-fd6f-40a2-8311-7224b5e59e0d", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "777.00", "ledger_id": "98c9907a-c652-48f3-8806-b62320522340", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "replay-3d9ffe", "reference": null, "company_id": "b98ec1dc-23db-40d8-b9d6-e8e144da5f43", "tds_amount": "0.00", "tds_section": null, "total_amount": "777.00", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	1a039168-8874-4404-b4c5-3f71f8b90910	api	2026-05-13 13:26:26.029313+00
992941c0-e98e-45a1-9e74-2b5d10d00c36	b98ec1dc-23db-40d8-b9d6-e8e144da5f43	956f8b68-30a0-42ee-9f8f-d4e52037c458	company.created	company	b98ec1dc-23db-40d8-b9d6-e8e144da5f43	null	{"id": "b98ec1dc-23db-40d8-b9d6-e8e144da5f43", "pan": null, "city": null, "name": "Acme-1e6c93", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	03b041db-39cb-4014-9b9b-d2993cb33fd5	api	2026-05-13 13:26:25.989171+00
ac16961d-0d90-4e2e-904d-2ad33adaa9f1	b98ec1dc-23db-40d8-b9d6-e8e144da5f43	956f8b68-30a0-42ee-9f8f-d4e52037c458	ledger.created	ledger	3b200045-fd6f-40a2-8311-7224b5e59e0d	null	{"id": "3b200045-fd6f-40a2-8311-7224b5e59e0d", "pan": null, "name": "Party-cdd6", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "b98ec1dc-23db-40d8-b9d6-e8e144da5f43", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-cdd6", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	8c4f75eb-2303-41fb-a8b7-39a560c13495	api	2026-05-13 13:26:26.016304+00
315fafca-9043-4007-a324-0bb81486b4c1	\N	fb92feda-96f4-412e-a999-a68fcd0bde1c	user.created	user	fb92feda-96f4-412e-a999-a68fcd0bde1c	null	{"id": "fb92feda-96f4-412e-a999-a68fcd0bde1c", "email": "v7user-628e0ac41c@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	2955908b-d0ce-468b-a186-7dd481cea5de	api	2026-05-13 13:26:28.278898+00
249c9e79-6f83-4333-be2c-78d1a7fb8dca	04a61e7b-797f-454b-911d-4c1948f93780	fb92feda-96f4-412e-a999-a68fcd0bde1c	company.created	company	04a61e7b-797f-454b-911d-4c1948f93780	null	{"id": "04a61e7b-797f-454b-911d-4c1948f93780", "pan": null, "city": null, "name": "Acme-2a8ebf", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	ac8487bf-c31a-47ac-b559-3c29848937d3	api	2026-05-13 13:26:28.64231+00
f943650a-e194-4490-abd9-380ee3e7eca4	04a61e7b-797f-454b-911d-4c1948f93780	fb92feda-96f4-412e-a999-a68fcd0bde1c	ledger.created	ledger	89ce969e-d3df-4727-a43a-de0889169906	null	{"id": "89ce969e-d3df-4727-a43a-de0889169906", "pan": null, "name": "Party-a135", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "04a61e7b-797f-454b-911d-4c1948f93780", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-a135", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	1bb9477b-6fdd-4d52-91c8-08315e37e176	api	2026-05-13 13:26:28.671197+00
571ec6eb-eb1b-4e8d-a59e-0159802167db	\N	2d78c851-fe2c-4ab9-83e8-5d5f511a67de	user.created	user	2d78c851-fe2c-4ab9-83e8-5d5f511a67de	null	{"id": "2d78c851-fe2c-4ab9-83e8-5d5f511a67de", "email": "v7user-5ce7d7ab8d@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	9f644533-393c-4135-bd22-20e3cc69b799	api	2026-05-13 13:26:30.917812+00
a13cd72a-8253-4d78-9714-53a14b8aa35b	c343c25d-cb4d-4461-8366-d18a7a433534	2d78c851-fe2c-4ab9-83e8-5d5f511a67de	company.created	company	c343c25d-cb4d-4461-8366-d18a7a433534	null	{"id": "c343c25d-cb4d-4461-8366-d18a7a433534", "pan": null, "city": null, "name": "Acme-193464", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	94f689d9-21ef-49fa-96a6-56d9f4b11278	api	2026-05-13 13:26:31.283354+00
fe199b1e-4a84-4d93-a2c5-fda7432637f8	c343c25d-cb4d-4461-8366-d18a7a433534	2d78c851-fe2c-4ab9-83e8-5d5f511a67de	ledger.created	ledger	47fdf2b9-ec52-471a-9e1f-486f38480495	null	{"id": "47fdf2b9-ec52-471a-9e1f-486f38480495", "pan": null, "name": "Party-b435", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "c343c25d-cb4d-4461-8366-d18a7a433534", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-b435", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	3f331bbb-ec2a-47d1-82ba-66eaf7b0c9f7	api	2026-05-13 13:26:31.312656+00
cd08ae70-5497-49ca-8f41-9c350f06c553	09b780ee-35f4-4d12-a8fc-ca07efd5bc00	27d19365-6c20-4347-9f87-724c94616e89	ledger.created	ledger	fda3ee6f-227d-43de-b7c3-92648f21cc05	null	{"id": "fda3ee6f-227d-43de-b7c3-92648f21cc05", "pan": null, "name": "Bank-ae22", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "09b780ee-35f4-4d12-a8fc-ca07efd5bc00", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-ae22", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	6024ffdf-c588-4d76-b7a1-6a10aa8ffc87	api	2026-05-13 13:26:33.920367+00
005ea8c7-26be-488e-b341-f42ff118301e	09b780ee-35f4-4d12-a8fc-ca07efd5bc00	27d19365-6c20-4347-9f87-724c94616e89	voucher.created	voucher	171d2b75-0b40-4c2e-9b65-431d8718a23d	null	{"id": "171d2b75-0b40-4c2e-9b65-431d8718a23d", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "777.00", "ledger_id": "584fa4f5-4bc1-482e-a58b-2d06aeb22ea5", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "777.00", "ledger_id": "fda3ee6f-227d-43de-b7c3-92648f21cc05", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "Validation suite test voucher", "reference": null, "company_id": "09b780ee-35f4-4d12-a8fc-ca07efd5bc00", "tds_amount": "0.00", "tds_section": null, "total_amount": "777.00", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	19b28546-4739-4f6f-b946-2baba752dc39	api	2026-05-13 13:26:33.948285+00
300d5c58-5d91-4098-a72e-258e95cad54d	74c493c5-e0ab-4802-9b5a-b64749462c43	57daf9e8-9922-4cc0-9710-fa70b1fd51dd	ledger.created	ledger	b7df9206-c941-45e0-8bc1-597f87cc500b	null	{"id": "b7df9206-c941-45e0-8bc1-597f87cc500b", "pan": null, "name": "Bank-e50a", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "74c493c5-e0ab-4802-9b5a-b64749462c43", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-e50a", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	8ac2b015-9be2-434d-b7e2-e7c44783c2c7	api	2026-05-13 13:26:36.558766+00
23e31759-ad90-4b09-af53-6740a713d89b	74c493c5-e0ab-4802-9b5a-b64749462c43	57daf9e8-9922-4cc0-9710-fa70b1fd51dd	voucher.created	voucher	5a4a30ee-4b65-49c6-8d82-426012f026ed	null	{"id": "5a4a30ee-4b65-49c6-8d82-426012f026ed", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "777.00", "ledger_id": "76ded8fa-df0c-44bc-a470-c3ecc7dd3b4f", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "777.00", "ledger_id": "b7df9206-c941-45e0-8bc1-597f87cc500b", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "count-probe-1356ed422c514d3f8419b910b8ba069f", "reference": null, "company_id": "74c493c5-e0ab-4802-9b5a-b64749462c43", "tds_amount": "0.00", "tds_section": null, "total_amount": "777.00", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	fafbe7a1-4291-479d-a756-4c4b92d0f967	api	2026-05-13 13:26:36.615238+00
9f9697be-9c80-4156-806e-65f4ed5bf575	04a61e7b-797f-454b-911d-4c1948f93780	fb92feda-96f4-412e-a999-a68fcd0bde1c	ledger.created	ledger	27b335dd-96c3-4645-8c8f-23d76be15f0b	null	{"id": "27b335dd-96c3-4645-8c8f-23d76be15f0b", "pan": null, "name": "Bank-c455", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "04a61e7b-797f-454b-911d-4c1948f93780", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-c455", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	98e02702-8e80-441e-97f8-e24adeb3cb5d	api	2026-05-13 13:26:28.657587+00
1097c6ff-4517-4ea1-92d6-7b8e57d8791d	04a61e7b-797f-454b-911d-4c1948f93780	fb92feda-96f4-412e-a999-a68fcd0bde1c	voucher.created	voucher	8b115e16-8f63-44cd-885a-0a1ca568b4a5	null	{"id": "8b115e16-8f63-44cd-885a-0a1ca568b4a5", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "100.00", "ledger_id": "27b335dd-96c3-4645-8c8f-23d76be15f0b", "narration": null, "entry_type": "Dr", "line_number": 1}, {"amount": "100.00", "ledger_id": "89ce969e-d3df-4727-a43a-de0889169906", "narration": null, "entry_type": "Cr", "line_number": 2}], "narration": "Validation suite test voucher", "reference": null, "company_id": "04a61e7b-797f-454b-911d-4c1948f93780", "tds_amount": "0.00", "tds_section": null, "total_amount": "100.00", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	python-httpx/0.28.1	08d6a04d-c30f-4943-b843-4d9dd2e9cddd	api	2026-05-13 13:26:28.687018+00
1121c82d-f5d5-4243-aabd-edc1d5b3726c	c343c25d-cb4d-4461-8366-d18a7a433534	2d78c851-fe2c-4ab9-83e8-5d5f511a67de	ledger.created	ledger	36f52252-f968-4584-b082-395f76bf01d1	null	{"id": "36f52252-f968-4584-b082-395f76bf01d1", "pan": null, "name": "Bank-0fcb", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "c343c25d-cb4d-4461-8366-d18a7a433534", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "bank-0fcb", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	71a04e6b-fa5b-4df5-a486-f302839667c7	api	2026-05-13 13:26:31.296903+00
c3e909ee-dfae-460f-b243-016b44280c7e	\N	27d19365-6c20-4347-9f87-724c94616e89	user.created	user	27d19365-6c20-4347-9f87-724c94616e89	null	{"id": "27d19365-6c20-4347-9f87-724c94616e89", "email": "v7user-94f67365ac@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	62ecd001-454d-48a7-bb6d-1be82827ee06	api	2026-05-13 13:26:33.5462+00
2a47680c-4b7f-4c0c-90da-a76eae83f6e0	09b780ee-35f4-4d12-a8fc-ca07efd5bc00	27d19365-6c20-4347-9f87-724c94616e89	company.created	company	09b780ee-35f4-4d12-a8fc-ca07efd5bc00	null	{"id": "09b780ee-35f4-4d12-a8fc-ca07efd5bc00", "pan": null, "city": null, "name": "Acme-7d3186", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	00d397df-5e39-4460-9d16-a0b6bf1e622f	api	2026-05-13 13:26:33.90681+00
54d4599b-4289-4ed5-8ffd-a720dd760d5c	09b780ee-35f4-4d12-a8fc-ca07efd5bc00	27d19365-6c20-4347-9f87-724c94616e89	ledger.created	ledger	584fa4f5-4bc1-482e-a58b-2d06aeb22ea5	null	{"id": "584fa4f5-4bc1-482e-a58b-2d06aeb22ea5", "pan": null, "name": "Party-d412", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "09b780ee-35f4-4d12-a8fc-ca07efd5bc00", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-d412", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	dadfb092-6a5f-4824-aa28-1464142c4a97	api	2026-05-13 13:26:33.934705+00
e428b120-4b80-46d6-91dd-b2f09608de89	\N	57daf9e8-9922-4cc0-9710-fa70b1fd51dd	user.created	user	57daf9e8-9922-4cc0-9710-fa70b1fd51dd	null	{"id": "57daf9e8-9922-4cc0-9710-fa70b1fd51dd", "email": "v7user-44ca6e5b76@example.com", "is_ca": false, "firm_name": null, "full_name": "Validation User", "is_active": true, "is_superuser": false, "ca_membership_no": null}	{}	127.0.0.1	python-httpx/0.28.1	455103dc-cfc8-4665-80a3-308cf57a23ff	api	2026-05-13 13:26:36.159696+00
40deff8c-6a43-417d-8ffb-243308d58a84	74c493c5-e0ab-4802-9b5a-b64749462c43	57daf9e8-9922-4cc0-9710-fa70b1fd51dd	company.created	company	74c493c5-e0ab-4802-9b5a-b64749462c43	null	{"id": "74c493c5-e0ab-4802-9b5a-b64749462c43", "pan": null, "city": null, "name": "Acme-d233f6", "gstin": null, "status": "active", "address": null, "pincode": null, "state_code": null, "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	127.0.0.1	python-httpx/0.28.1	2c6f9d55-e6cb-49e4-bae7-2bfe1b41f4aa	api	2026-05-13 13:26:36.537433+00
59efe500-bc4b-425d-a506-c89c32368229	74c493c5-e0ab-4802-9b5a-b64749462c43	57daf9e8-9922-4cc0-9710-fa70b1fd51dd	ledger.created	ledger	76ded8fa-df0c-44bc-a470-c3ecc7dd3b4f	null	{"id": "76ded8fa-df0c-44bc-a470-c3ecc7dd3b4f", "pan": null, "name": "Party-8dfd", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "74c493c5-e0ab-4802-9b5a-b64749462c43", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "party-8dfd", "opening_balance": "0.00", "parent_ledger_id": null}	{}	127.0.0.1	python-httpx/0.28.1	9353694b-ac25-4921-bd19-d10cdfd01672	api	2026-05-13 13:26:36.573384+00
c1cf99fb-7ed5-446e-b77a-267e831ff07e	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	company.created	company	58f01ad7-88ad-4eb5-bf49-664a665185f7	null	{"id": "58f01ad7-88ad-4eb5-bf49-664a665185f7", "pan": null, "city": null, "name": "Taxmind Books", "gstin": "27ALIPC7943D1Z0", "status": "active", "address": null, "pincode": null, "state_code": "27", "accounting_source": "standalone", "financial_year_start": "2026-04-01"}	{}	192.168.1.38	okhttp/4.9.2	b44741cd-a0f9-40ef-aaf2-ffb17f890c17	api	2026-05-14 10:35:44.764267+00
858d3bfc-d520-4a02-9fcf-a808c73212d5	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	ledger.created	ledger	de4f6c7c-d5b3-4c09-8a0b-98fe2beb71b7	null	{"id": "de4f6c7c-d5b3-4c09-8a0b-98fe2beb71b7", "pan": null, "name": "ABC LTD", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "58f01ad7-88ad-4eb5-bf49-664a665185f7", "group_name": "Sundry Creditors", "state_code": null, "balance_type": "Dr", "name_normalized": "abc ltd", "opening_balance": "0", "parent_ledger_id": null}	{}	\N	connector-sync/1.0	44f64784-bfda-4667-b793-771189a8ca0a	connector	2026-05-16 10:59:15.285235+00
3ffdaa99-6e66-4949-a084-41691cd09ac4	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	ledger.created	ledger	35bc6d27-a185-4c02-bf8a-0c5c568e7068	null	{"id": "35bc6d27-a185-4c02-bf8a-0c5c568e7068", "pan": null, "name": "Cash", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "58f01ad7-88ad-4eb5-bf49-664a665185f7", "group_name": "Cash-in-Hand", "state_code": null, "balance_type": "Dr", "name_normalized": "cash", "opening_balance": "0", "parent_ledger_id": null}	{}	\N	connector-sync/1.0	44f64784-bfda-4667-b793-771189a8ca0a	connector	2026-05-16 10:59:15.285235+00
7162f276-9590-488f-bb26-af95167477ab	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	ledger.created	ledger	47b8d9d4-9797-4539-955e-6a38d41a7b70	null	{"id": "47b8d9d4-9797-4539-955e-6a38d41a7b70", "pan": null, "name": "HDFC BANK", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "58f01ad7-88ad-4eb5-bf49-664a665185f7", "group_name": "Bank Accounts", "state_code": null, "balance_type": "Dr", "name_normalized": "hdfc bank", "opening_balance": "0", "parent_ledger_id": null}	{}	\N	connector-sync/1.0	44f64784-bfda-4667-b793-771189a8ca0a	connector	2026-05-16 10:59:15.285235+00
4a355c97-7976-4550-a3e2-f582c7739eac	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	ledger.created	ledger	fc5ffae9-50eb-499e-a534-694b0978745e	null	{"id": "fc5ffae9-50eb-499e-a534-694b0978745e", "pan": null, "name": "Profit & Loss A/c", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "58f01ad7-88ad-4eb5-bf49-664a665185f7", "group_name": "Primary", "state_code": null, "balance_type": "Dr", "name_normalized": "profit & loss a/c", "opening_balance": "0", "parent_ledger_id": null}	{}	\N	connector-sync/1.0	44f64784-bfda-4667-b793-771189a8ca0a	connector	2026-05-16 10:59:15.285235+00
7beebc9d-fbe0-4833-ac17-617137debeeb	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	ledger.created	ledger	8d919437-2a12-43ba-bb80-ac30459ae4b7	null	{"id": "8d919437-2a12-43ba-bb80-ac30459ae4b7", "pan": null, "name": "Xyz Ltd", "email": null, "gstin": null, "phone": null, "address": null, "is_active": true, "company_id": "58f01ad7-88ad-4eb5-bf49-664a665185f7", "group_name": "Sundry Debtors", "state_code": null, "balance_type": "Dr", "name_normalized": "xyz ltd", "opening_balance": "0", "parent_ledger_id": null}	{}	\N	connector-sync/1.0	44f64784-bfda-4667-b793-771189a8ca0a	connector	2026-05-16 10:59:15.285235+00
bb050fcd-9bb2-4443-9130-28080fd2080a	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	voucher.created	voucher	7a7f4f81-8f96-4a2e-945e-e5146b567329	null	{"id": "7a7f4f81-8f96-4a2e-945e-e5146b567329", "cess": "0.00", "cgst": "0.00", "date": "2026-05-16", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"amount": "1000.00", "ledger_id": "35bc6d27-a185-4c02-bf8a-0c5c568e7068", "narration": null, "entry_type": "Cr", "line_number": 2}, {"amount": "1000.00", "ledger_id": "de4f6c7c-d5b3-4c09-8a0b-98fe2beb71b7", "narration": null, "entry_type": "Dr", "line_number": 1}], "narration": "Test voucher posted while Tally stopped", "reference": null, "company_id": "58f01ad7-88ad-4eb5-bf49-664a665185f7", "tds_amount": "0.00", "tds_section": null, "total_amount": "1000.00", "voucher_type": "Payment", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	127.0.0.1	Mozilla/5.0 (Windows NT; Windows NT 10.0; en-US) WindowsPowerShell/5.1.26100.8457	10de0220-3b82-48c2-9645-07fd3a99a525	api	2026-05-16 11:07:26.213469+00
\.


--
-- Data for Name: companies; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.companies (id, name, gstin, pan, financial_year_start, accounting_source, status, address, city, state_code, pincode, created_by, created_at, updated_at) FROM stdin;
e6ad6664-9dd8-4a58-93cb-da56e4cbd3aa	Acme-4a304b	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	ec471c79-a85d-4873-bd8d-ee08da19707e	2026-05-13 13:25:40.382895+00	2026-05-13 13:25:40.382895+00
07eed054-9a72-40bf-89bc-592568fd1d26	Acme-228b91	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	85cda290-e587-4637-bc95-032c5fadfe1e	2026-05-13 13:25:43.192839+00	2026-05-13 13:25:43.192839+00
69e53193-f10f-48d1-880f-e4745b39ed47	Acme-695d70	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	05256d5b-fcc6-4c2c-a991-fd36d23d6504	2026-05-13 13:25:46.475853+00	2026-05-13 13:25:46.475853+00
a8eea1e3-8697-4bdd-a735-e0d1222816d6	Acme-b972b7	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	f7eaecff-8db0-466b-a734-74294dca8654	2026-05-13 13:25:49.261053+00	2026-05-13 13:25:49.261053+00
73c6c8f6-5d56-473a-92cc-88e036e6f515	Acme-b0c531	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	abfdd8f5-32d5-4358-933a-c786fb875466	2026-05-13 13:25:52.073136+00	2026-05-13 13:25:52.073136+00
908c5157-d04e-40e6-b01e-e3ba44912beb	Acme-608dff	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	08d3d460-f4d5-45c2-96c5-7d2f4e85e2b5	2026-05-13 13:25:52.505195+00	2026-05-13 13:25:52.505195+00
d73af30c-8808-471d-83ca-553d0163247f	Acme-29c170	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	df5b8c8b-2083-40fb-8815-e3b289b88f81	2026-05-13 13:25:55.073358+00	2026-05-13 13:25:55.073358+00
b6b8b865-f868-485b-9a5d-74964a413d45	Acme-ebe9de	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	19b0ceeb-b97f-4ba4-9d13-6017f516d171	2026-05-13 13:25:55.466148+00	2026-05-13 13:25:55.466148+00
2b0bea11-d862-401f-b128-ec6e48cec60d	Acme-a5f508	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	5f0759b7-260a-4a7a-a61e-c113939f9206	2026-05-13 13:25:58.061536+00	2026-05-13 13:25:58.061536+00
ebbf6003-37d5-4265-9184-8fdfaba5793a	Acme-1c1240	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	a60dfcad-bf58-49c4-9435-fc9b1de6523c	2026-05-13 13:25:58.524947+00	2026-05-13 13:25:58.524947+00
60ca15f3-0e5c-432b-8b0d-a77d783129b3	Acme-f9a530	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	d072f2df-465f-4aaf-bbbf-663ccbe52dbe	2026-05-13 13:26:01.096768+00	2026-05-13 13:26:01.096768+00
28819b28-ffd6-4877-b097-d68542903509	Acme-80ec7a	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	8d40beaf-87e9-47f6-8f8f-68e350011e76	2026-05-13 13:26:03.708057+00	2026-05-13 13:26:03.708057+00
fde328ce-64f3-4555-b472-d0a0f0ab7313	Acme-5d40f9	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	e53e23ba-5809-4965-a6e2-129f3c7682f1	2026-05-13 13:26:04.133318+00	2026-05-13 13:26:04.133318+00
c9701980-a203-4995-81db-50eb351db7cd	Acme-7432cc	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	781417d6-2f31-4388-a3d1-19ea97d14754	2026-05-13 13:26:06.774564+00	2026-05-13 13:26:06.774564+00
7ddbbff3-244f-42c3-b142-530b02e7f739	Acme-ef7434	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	beccb035-b15f-4e8f-98dc-bba5381522c2	2026-05-13 13:26:09.52884+00	2026-05-13 13:26:09.52884+00
09214117-1d79-4d43-b074-028661f423bd	Acme-5e3133	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	00b34604-0adb-4950-b38c-25b0d81424f6	2026-05-13 13:26:12.229119+00	2026-05-13 13:26:12.229119+00
8ad00365-70bb-40bb-8f0c-e735597dcf86	Acme-a30660	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2	2026-05-13 13:26:14.914642+00	2026-05-13 13:26:14.914642+00
9f6ced8d-775b-4061-bde5-7098fc05112a	Acme-45f6e6	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	aaddd93f-258f-46d1-8e79-6c91ca0c6e6f	2026-05-13 13:26:17.595625+00	2026-05-13 13:26:17.595625+00
6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	Acme-e50dd3	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	e5e8c459-79f3-4ac7-b2db-cee90c388ba8	2026-05-13 13:26:20.579889+00	2026-05-13 13:26:20.579889+00
249c8bde-f26e-4e5f-8caa-e5b1f476c86d	Acme-dffd7b	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	1b75c626-594e-4a15-87a3-0e6ad41958d1	2026-05-13 13:26:23.351868+00	2026-05-13 13:26:23.351868+00
b98ec1dc-23db-40d8-b9d6-e8e144da5f43	Acme-1e6c93	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	956f8b68-30a0-42ee-9f8f-d4e52037c458	2026-05-13 13:26:25.989171+00	2026-05-13 13:26:25.989171+00
04a61e7b-797f-454b-911d-4c1948f93780	Acme-2a8ebf	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	fb92feda-96f4-412e-a999-a68fcd0bde1c	2026-05-13 13:26:28.64231+00	2026-05-13 13:26:28.64231+00
c343c25d-cb4d-4461-8366-d18a7a433534	Acme-193464	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	2d78c851-fe2c-4ab9-83e8-5d5f511a67de	2026-05-13 13:26:31.283354+00	2026-05-13 13:26:31.283354+00
09b780ee-35f4-4d12-a8fc-ca07efd5bc00	Acme-7d3186	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	27d19365-6c20-4347-9f87-724c94616e89	2026-05-13 13:26:33.90681+00	2026-05-13 13:26:33.90681+00
74c493c5-e0ab-4802-9b5a-b64749462c43	Acme-d233f6	\N	\N	2026-04-01	standalone	active	\N	\N	\N	\N	57daf9e8-9922-4cc0-9710-fa70b1fd51dd	2026-05-13 13:26:36.537433+00	2026-05-13 13:26:36.537433+00
58f01ad7-88ad-4eb5-bf49-664a665185f7	Taxmind Books	27ALIPC7943D1Z0	\N	2026-04-01	standalone	active	\N	\N	27	\N	79910b81-2a28-4fa4-8745-ee9d468a65bc	2026-05-14 10:35:44.764267+00	2026-05-14 10:35:44.764267+00
\.


--
-- Data for Name: connector_enrollment_codes; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.connector_enrollment_codes (id, company_id, created_by, code_hash, consumed_at, expires_at, created_at) FROM stdin;
e2e93785-a396-4418-91a7-63b4d5de2b53	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	57260523dce46c600e96bdad54e60c4caf8e8300d42c6d424d4183e2ba875f55	2026-05-14 11:38:42.730936+00	2026-05-14 11:53:27.511175+00	2026-05-14 11:38:27.505896+00
3dd14719-a5f6-416f-a2ea-b31753ecef98	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	4b1492a24600ee8c1121677397030d99dc981a7db81bc40fc84af8b22e467057	2026-05-14 13:13:57.118548+00	2026-05-14 13:28:57.06262+00	2026-05-14 13:13:57.052909+00
ec32874f-e09b-41ab-a1b3-3b64ebc183c0	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	a1301b1d14ea272508687ad5998350a6d01e8726824b0bb44946e1727b79a64f	2026-05-15 13:18:50.002675+00	2026-05-15 13:33:49.950544+00	2026-05-15 13:18:49.944295+00
5952fc72-1477-4f73-b75f-d3f9018470b0	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	02b1346fa8c0829519a10f39a2864edea57e43c73e62d3ef29e1f69ea76c748f	2026-05-16 10:22:46.038647+00	2026-05-16 10:37:45.889279+00	2026-05-16 10:22:45.872752+00
96254159-b496-4054-9031-17cbc601b4b6	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	c5eeb1eeeb5ae9e69b1ffa6f2fdef2fdc5e2acce46f89048e648904d14ebef00	2026-05-16 10:58:11.218863+00	2026-05-16 11:13:11.19664+00	2026-05-16 10:58:11.190772+00
cd3e2af9-67e7-48f9-a424-311469120da5	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	9dcc9f34e2537f0e3ad10a6d0901d0fa40e026f25a6558651a86d72f0b6ccc5a	2026-05-16 10:58:57.233465+00	2026-05-16 11:13:57.199342+00	2026-05-16 10:58:57.19431+00
\.


--
-- Data for Name: device_tokens; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.device_tokens (id, user_id, token, platform, app_version, is_active, last_active_at, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: idempotency_keys; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.idempotency_keys (id, company_id, user_id, key, method, path, request_hash, response_status, response_body, response_headers, locked_at, completed_at, created_at, expires_at) FROM stdin;
01a051be-1701-41f9-974e-acdf6eaad66d	07eed054-9a72-40bf-89bc-592568fd1d26	85cda290-e587-4637-bc95-032c5fadfe1e	61be2944bb17482b93db2869ed4de8be	POST	/api/v1/vouchers/	7b2351cabe5809e5519d9d7b4e553e6e70022e1f98f9c9b981f388216fa718b8	201	{"id": "6a113817-48c9-4f0e-a299-bb8cbb5e2386", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "5910ca24-e637-42f4-947d-f168ce106fac", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "74efb1df-8798-4da2-b16b-029163dec4dc", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "c27b76c8-ad9c-45d6-8b8d-903f2d47f8a5", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "bbf818e8-a117-4eef-896c-f99c741e7736", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "Validation suite test voucher", "reference": null, "company_id": "07eed054-9a72-40bf-89bc-592568fd1d26", "created_at": "2026-05-13T13:25:43.231599Z", "created_by": "85cda290-e587-4637-bc95-032c5fadfe1e", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:25:43.23451+00	2026-05-13 13:25:43.260126+00	2026-05-13 13:25:43.231599+00	2026-05-14 13:25:43.234511+00
dc31c014-04d6-48fe-a027-e4b9490a03e3	a8eea1e3-8697-4bdd-a735-e0d1222816d6	f7eaecff-8db0-466b-a734-74294dca8654	48e013bd27e94b389f0b9aaaebcbc379	POST	/api/v1/vouchers/	b887c9754f5cc3df7d7bdbecb3ae20f5e4d1304b69f9c3726fad92afa97266d7	201	{"id": "2e5f9e23-b025-47a9-8007-efba746dd2dd", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "3c0b5bf1-9611-499f-8176-9d002d4f9398", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "30f7c49b-7ced-4625-a59f-2714d18eca80", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "77f5baa3-b5aa-4baa-9938-cb211984f63e", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "c2a20c2a-f943-47b7-9e2d-f552f5712fdc", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "Validation suite test voucher", "reference": null, "company_id": "a8eea1e3-8697-4bdd-a735-e0d1222816d6", "created_at": "2026-05-13T13:25:49.396815Z", "created_by": "f7eaecff-8db0-466b-a734-74294dca8654", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:25:49.397848+00	2026-05-13 13:25:49.408275+00	2026-05-13 13:25:49.396815+00	2026-05-14 13:25:49.39785+00
be05f66e-756f-466b-b5b1-b9843719329b	73c6c8f6-5d56-473a-92cc-88e036e6f515	abfdd8f5-32d5-4358-933a-c786fb875466	7fb3edc7e63e4ae5b6d2da08bd7fa50b	POST	/api/v1/vouchers/	cba8514ff4fa13955429f81fddc98875f89f229f371ba580835d73b1c917310e	201	{"id": "4db81436-b985-49f7-97b3-17e3e3a19022", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "5d221d81-2085-4c36-a35c-29ddce893fbd", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "0ba833dd-2220-4be7-82d3-ff2dc592ca55", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "45e7e336-5524-4bb7-a406-d89aef1872c6", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "cab8fd0a-8c4d-4364-b024-fabec883e579", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "Validation suite test voucher", "reference": null, "company_id": "73c6c8f6-5d56-473a-92cc-88e036e6f515", "created_at": "2026-05-13T13:25:52.117945Z", "created_by": "abfdd8f5-32d5-4358-933a-c786fb875466", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:25:52.118817+00	2026-05-13 13:25:52.130416+00	2026-05-13 13:25:52.117945+00	2026-05-14 13:25:52.11882+00
52aaf3e4-25d5-4909-b736-76955f6d0a2f	2b0bea11-d862-401f-b128-ec6e48cec60d	5f0759b7-260a-4a7a-a61e-c113939f9206	23d9ef1a34494e8580b152b9c0723be9	POST	/api/v1/vouchers/	a2adf58de8a906986741b9732d4fddbd8f6a23874b2ec8d808dbcff29b28551c	201	{"id": "2afeee51-7b03-4d45-a4ef-54917c6d3964", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "0facc024-286b-4606-8289-09501beed395", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "c42a75a0-77bc-4eb7-950a-ef322a6713a2", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "0e57e5c6-269b-4d78-b298-851cef7cdf4e", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "df3d3586-7f3f-4cba-9628-705650a69902", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "Validation suite test voucher", "reference": null, "company_id": "2b0bea11-d862-401f-b128-ec6e48cec60d", "created_at": "2026-05-13T13:25:58.539514Z", "created_by": "5f0759b7-260a-4a7a-a61e-c113939f9206", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:25:58.540755+00	2026-05-13 13:25:58.552965+00	2026-05-13 13:25:58.539514+00	2026-05-14 13:25:58.540757+00
67ed8c44-453a-4b4f-8d3b-aa2a6067650e	28819b28-ffd6-4877-b097-d68542903509	8d40beaf-87e9-47f6-8f8f-68e350011e76	c7a9ca8758fd4fdfb795c4a306652024	POST	/api/v1/vouchers/	928c4f8172efabe2e19092fac3d2b15c7e03461a4902771f290dfeddbfc3c968	201	{"id": "1a2c939b-7efd-4e6f-8ff1-cb15504883c0", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "05facb5a-db0b-42be-a66b-406488a1840b", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "f4d38d67-5532-4f4e-b4be-01afc7b06bbe", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "38b7f582-f618-40a3-85ea-71417c78f1ce", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "9a71b5a6-fcae-40ff-ad15-5989c8fc160f", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "company-A row", "reference": null, "company_id": "28819b28-ffd6-4877-b097-d68542903509", "created_at": "2026-05-13T13:26:03.756565Z", "created_by": "8d40beaf-87e9-47f6-8f8f-68e350011e76", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:26:03.756777+00	2026-05-13 13:26:03.768175+00	2026-05-13 13:26:03.756565+00	2026-05-14 13:26:03.756778+00
2337024b-c19d-4f9c-ac8c-2019f38f6650	09214117-1d79-4d43-b074-028661f423bd	00b34604-0adb-4950-b38c-25b0d81424f6	ee92a1c8a0e74f14a91fc92400be503d	POST	/api/v1/vouchers/	cea82b66bbe698913583688383f6484a232a178b58673b1b88bae34c44391c1f	201	{"id": "4c09ff5c-fc40-430e-900c-fdf3733e808b", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "50519e90-99eb-41e9-8759-9bdfe25c2b33", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "704fe26c-4c5f-446b-80ef-9d959252c61a", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "72d14452-2825-4da0-891b-1ce57c73e494", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "0e3c1f4e-04ce-42a3-8c68-6461f31d0727", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "Validation suite test voucher", "reference": null, "company_id": "09214117-1d79-4d43-b074-028661f423bd", "created_at": "2026-05-13T13:26:12.272876Z", "created_by": "00b34604-0adb-4950-b38c-25b0d81424f6", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:26:12.274229+00	2026-05-13 13:26:12.286372+00	2026-05-13 13:26:12.272876+00	2026-05-14 13:26:12.274231+00
7c90f489-855e-44d6-aee6-dfd020d45748	8ad00365-70bb-40bb-8f0c-e735597dcf86	0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2	22b0130bcac14b0593958d08269f55ea	POST	/api/v1/vouchers/	a2cde894d9bb852056b0dcb084e650078377c8c18942272959839f80ddb19af3	201	{"id": "10f24d67-945f-43e8-aca1-5ceed49397f5", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "698cdb9d-3c2e-4388-817b-4c40fbbf04d3", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "3f38cd90-4c4b-4da7-9f4a-3294616f8704", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "542ea446-f0f2-477a-8a41-095ca87ed6bd", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "332aa823-d64d-48fb-b908-4f4768621731", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "Validation suite test voucher", "reference": null, "company_id": "8ad00365-70bb-40bb-8f0c-e735597dcf86", "created_at": "2026-05-13T13:26:14.978365Z", "created_by": "0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:26:14.980204+00	2026-05-13 13:26:14.992334+00	2026-05-13 13:26:14.978365+00	2026-05-14 13:26:14.980207+00
e1aeb166-ba48-4eb7-a36c-06556586fd7e	249c8bde-f26e-4e5f-8caa-e5b1f476c86d	1b75c626-594e-4a15-87a3-0e6ad41958d1	idem-942641ede15a4aac9fcd86701d3e8b8f	POST	/api/v1/vouchers/	c3bc24a456a8cdf12e2d229f5a09cf5d6618da3c362fcfd9d7717f61da8e4091	201	{"id": "aa00e22a-bd24-4105-a89e-e51723c0e0bf", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "ebf59508-a9eb-4ae7-b971-09da3a6bb0d9", "cgst": null, "igst": null, "sgst": null, "amount": "777.00", "gst_rate": null, "ledger_id": "9ea79806-0a6f-4501-9db8-78df81083bfe", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "692b3ba0-87d2-4d06-adff-33222e6b69b0", "cgst": null, "igst": null, "sgst": null, "amount": "777.00", "gst_rate": null, "ledger_id": "a8773b36-b3b6-498a-ada6-6abd30726546", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "Validation suite test voucher", "reference": null, "company_id": "249c8bde-f26e-4e5f-8caa-e5b1f476c86d", "created_at": "2026-05-13T13:26:23.392231Z", "created_by": "1b75c626-594e-4a15-87a3-0e6ad41958d1", "tds_amount": "0.00", "tds_section": null, "total_amount": "777.00", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:26:23.393305+00	2026-05-13 13:26:23.404566+00	2026-05-13 13:26:23.392231+00	2026-05-14 13:26:23.393307+00
ed18ccdf-4801-4bb7-8133-e93bba886461	fde328ce-64f3-4555-b472-d0a0f0ab7313	e53e23ba-5809-4965-a6e2-129f3c7682f1	01749c61cb6b4575a282a9f476d5f075	POST	/api/v1/vouchers/	a86f0da0fc4c9bebd0fc38baf416ee2a265fccf4600e507d20acfb0126ffce46	201	{"id": "0f5b7e2a-6032-4765-ad1f-355b0c01919e", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "c4a26da2-57b1-4aa8-8788-a2263a99c4e6", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "4163e6de-d7ad-42c6-8156-f41807a72e9c", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "e82883fd-9d9a-47d4-a95f-f7b846784e78", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "c308bfc6-2bca-4bc9-ba96-0abaf2606a1e", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "company-B row", "reference": null, "company_id": "fde328ce-64f3-4555-b472-d0a0f0ab7313", "created_at": "2026-05-13T13:26:04.181062Z", "created_by": "e53e23ba-5809-4965-a6e2-129f3c7682f1", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:26:04.18154+00	2026-05-13 13:26:04.193537+00	2026-05-13 13:26:04.181062+00	2026-05-14 13:26:04.181542+00
c0e1fab9-03f2-46e3-8fb8-700b8302b0c3	c9701980-a203-4995-81db-50eb351db7cd	781417d6-2f31-4388-a3d1-19ea97d14754	eaaaf72eaac74d009600559d4d6943f5	POST	/api/v1/vouchers/	aed0a90df29ed5b4816fd1ef58e0e730503385e0745c5a3c2a6084e99df4e041	201	{"id": "2f7c9ed9-f337-413b-8119-5cd04343c1a1", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "f8a4a922-c13a-4e03-ba68-29821397fbd0", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "42171f00-a457-4b71-9f44-710030c9b921", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "cae45298-f8c0-464f-8f6a-0b8b5817e716", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "fa4d102a-ef67-4ce4-8922-58c6bf860a41", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "Validation suite test voucher", "reference": null, "company_id": "c9701980-a203-4995-81db-50eb351db7cd", "created_at": "2026-05-13T13:26:06.814545Z", "created_by": "781417d6-2f31-4388-a3d1-19ea97d14754", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:26:06.814634+00	2026-05-13 13:26:06.824529+00	2026-05-13 13:26:06.814545+00	2026-05-14 13:26:06.814635+00
8245be07-9e65-48ef-9334-9d2d052e3364	7ddbbff3-244f-42c3-b142-530b02e7f739	beccb035-b15f-4e8f-98dc-bba5381522c2	36913d546a2e4f9883ed2d6e20be336c	POST	/api/v1/vouchers/	1f8c2be01e2054740fbc68544e1811ac3aa9c6da65951a09f1c28f8843a94a1e	201	{"id": "c5a3602f-5caa-4010-9354-162303de746d", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "6e41d45b-a084-4cbc-b1bd-ca45d13c41fe", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "adf6af3c-c451-4faf-9c68-b212aee4eaf8", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "777e1cac-fc53-4922-bd9b-47f1f8e54941", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "71c0a0a6-afcf-48c2-ae41-ea4c1dc8c70e", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "original narration", "reference": null, "company_id": "7ddbbff3-244f-42c3-b142-530b02e7f739", "created_at": "2026-05-13T13:26:09.585838Z", "created_by": "beccb035-b15f-4e8f-98dc-bba5381522c2", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:26:09.585924+00	2026-05-13 13:26:09.597907+00	2026-05-13 13:26:09.585838+00	2026-05-14 13:26:09.585926+00
2fc3deaf-c11a-401b-a43a-5dafa99ca4ba	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	e5e8c459-79f3-4ac7-b2db-cee90c388ba8	7136806f76fe415c85c3b3a56a5ab01e	POST	/api/v1/vouchers/	9efb23f5b83b53df9acc0c05a2b5aa7324ec4d80d14511b71d2a094de8b91007	201	{"id": "374079a3-0845-4488-aeec-439ce8e056fc", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "89ba2a82-e324-4aa9-89c0-55570d2c3546", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "84142669-e981-4380-ad1e-01ea3792d93a", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "de684b4f-376c-4207-b830-ae0889b75a22", "cgst": null, "igst": null, "sgst": null, "amount": "1500.99", "gst_rate": null, "ledger_id": "86d3c0f6-f61d-4c8b-bcef-fa12c710a83d", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "Validation suite test voucher", "reference": null, "company_id": "6f1e7c99-48ec-42ec-8a78-111a5dd0f0af", "created_at": "2026-05-13T13:26:20.621447Z", "created_by": "e5e8c459-79f3-4ac7-b2db-cee90c388ba8", "tds_amount": "0.00", "tds_section": null, "total_amount": "1500.99", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:26:20.62252+00	2026-05-13 13:26:20.632752+00	2026-05-13 13:26:20.621447+00	2026-05-14 13:26:20.622521+00
1523847f-c8d5-4a24-a072-91b54d07791c	b98ec1dc-23db-40d8-b9d6-e8e144da5f43	956f8b68-30a0-42ee-9f8f-d4e52037c458	idem-b8ba90034f4e4b928a9bc320439c5b5e	POST	/api/v1/vouchers/	9ebeca46717689ac2e67bc17b087f6a454c11331d5c34aa586e4f02f75576a8e	201	{"id": "923a0f8b-974b-4f68-bf6f-f7ca16565910", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "f2dda572-be30-4b05-8ac3-c3f56e0c101b", "cgst": null, "igst": null, "sgst": null, "amount": "777.00", "gst_rate": null, "ledger_id": "98c9907a-c652-48f3-8806-b62320522340", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "cb98cddd-5415-4b5a-bfbb-15ad8cabdeb8", "cgst": null, "igst": null, "sgst": null, "amount": "777.00", "gst_rate": null, "ledger_id": "3b200045-fd6f-40a2-8311-7224b5e59e0d", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "replay-3d9ffe", "reference": null, "company_id": "b98ec1dc-23db-40d8-b9d6-e8e144da5f43", "created_at": "2026-05-13T13:26:26.029313Z", "created_by": "956f8b68-30a0-42ee-9f8f-d4e52037c458", "tds_amount": "0.00", "tds_section": null, "total_amount": "777.00", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:26:26.030084+00	2026-05-13 13:26:26.040075+00	2026-05-13 13:26:26.029313+00	2026-05-14 13:26:26.030085+00
dd952e1f-3ede-400b-8507-6690236291b6	04a61e7b-797f-454b-911d-4c1948f93780	fb92feda-96f4-412e-a999-a68fcd0bde1c	idem-eeff97d5b6eb4bae958838b3dbf794ee	POST	/api/v1/vouchers/	8291378e2f4ddad374d483857d2bd904cf8fa093bcfa303332f3635457ab1381	201	{"id": "8b115e16-8f63-44cd-885a-0a1ca568b4a5", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "9be1740d-a443-4e90-b973-71dcc32782bb", "cgst": null, "igst": null, "sgst": null, "amount": "100.00", "gst_rate": null, "ledger_id": "27b335dd-96c3-4645-8c8f-23d76be15f0b", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "875079e0-5b96-4b74-924d-06de9aef90d3", "cgst": null, "igst": null, "sgst": null, "amount": "100.00", "gst_rate": null, "ledger_id": "89ce969e-d3df-4727-a43a-de0889169906", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "Validation suite test voucher", "reference": null, "company_id": "04a61e7b-797f-454b-911d-4c1948f93780", "created_at": "2026-05-13T13:26:28.687018Z", "created_by": "fb92feda-96f4-412e-a999-a68fcd0bde1c", "tds_amount": "0.00", "tds_section": null, "total_amount": "100.00", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:26:28.687705+00	2026-05-13 13:26:28.698148+00	2026-05-13 13:26:28.687018+00	2026-05-14 13:26:28.687706+00
e72b3e21-f99f-4d7c-b0ad-74fe7b5f61c9	09b780ee-35f4-4d12-a8fc-ca07efd5bc00	27d19365-6c20-4347-9f87-724c94616e89	idem-5e66e83d796340aaa221ec79fd5f5f24	POST	/api/v1/vouchers/	21e1acb5c2d0376397a55bc49266cf4e69d87035d2543b9b20f59078871dcbb9	201	{"id": "171d2b75-0b40-4c2e-9b65-431d8718a23d", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "d39b1d94-87b8-4cd4-b5da-53f17f7a1601", "cgst": null, "igst": null, "sgst": null, "amount": "777.00", "gst_rate": null, "ledger_id": "fda3ee6f-227d-43de-b7c3-92648f21cc05", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "7403fcf6-e034-434c-b32e-2efad7d2da77", "cgst": null, "igst": null, "sgst": null, "amount": "777.00", "gst_rate": null, "ledger_id": "584fa4f5-4bc1-482e-a58b-2d06aeb22ea5", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "Validation suite test voucher", "reference": null, "company_id": "09b780ee-35f4-4d12-a8fc-ca07efd5bc00", "created_at": "2026-05-13T13:26:33.948285Z", "created_by": "27d19365-6c20-4347-9f87-724c94616e89", "tds_amount": "0.00", "tds_section": null, "total_amount": "777.00", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:26:33.948738+00	2026-05-13 13:26:33.95931+00	2026-05-13 13:26:33.948285+00	2026-05-14 13:26:33.94874+00
8b8de597-0597-406e-b864-7cdda1c196f3	74c493c5-e0ab-4802-9b5a-b64749462c43	57daf9e8-9922-4cc0-9710-fa70b1fd51dd	idem-ad156e311cdd415aa00c3530c00b36c3	POST	/api/v1/vouchers/	9f742867b34c18245327580e3a52efd335d193029750776d2f976f13a3d87ac8	201	{"id": "5a4a30ee-4b65-49c6-8d82-426012f026ed", "cess": "0.00", "cgst": "0.00", "date": "2026-05-08", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "17d943ee-0118-48fe-8c2d-c3a045c47de7", "cgst": null, "igst": null, "sgst": null, "amount": "777.00", "gst_rate": null, "ledger_id": "b7df9206-c941-45e0-8bc1-597f87cc500b", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "76481e65-903f-4359-8166-ebae705fa021", "cgst": null, "igst": null, "sgst": null, "amount": "777.00", "gst_rate": null, "ledger_id": "76ded8fa-df0c-44bc-a470-c3ecc7dd3b4f", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "count-probe-1356ed422c514d3f8419b910b8ba069f", "reference": null, "company_id": "74c493c5-e0ab-4802-9b5a-b64749462c43", "created_at": "2026-05-13T13:26:36.615238Z", "created_by": "57daf9e8-9922-4cc0-9710-fa70b1fd51dd", "tds_amount": "0.00", "tds_section": null, "total_amount": "777.00", "voucher_type": "Receipt", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-13 13:26:36.615829+00	2026-05-13 13:26:36.626332+00	2026-05-13 13:26:36.615238+00	2026-05-14 13:26:36.615831+00
b5b6e4e8-35c9-4dab-9bea-e952a0867c9f	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	f433a29a-2122-4fa1-85c9-94e06bda2fd3	POST	/api/v1/connector/sync/58f01ad7-88ad-4eb5-bf49-664a665185f7	44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a	202	{"status": "sync_triggered", "task_id": "56c46032-3b09-4092-9bc6-84ffcde8190c", "estimated_duration_seconds": 30}	{}	2026-05-15 13:44:28.696599+00	2026-05-15 13:44:28.70072+00	2026-05-15 13:44:28.688811+00	2026-05-16 13:44:28.696602+00
b7876a8a-4a4f-4891-860b-d39a24075583	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	d0776e77-b04c-4328-9f31-e660d0a1c812	POST	/api/v1/connector/sync/58f01ad7-88ad-4eb5-bf49-664a665185f7	44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a	202	{"status": "sync_triggered", "task_id": "f5a0216c-184d-4c1c-887e-4f852c2ff2a5", "estimated_duration_seconds": 30}	{}	2026-05-15 14:00:25.421618+00	2026-05-15 14:00:25.423589+00	2026-05-15 14:00:25.419332+00	2026-05-16 14:00:25.42162+00
7c853a07-7456-4759-b54b-3e1001a030b4	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	d40a8038-2f2c-4388-85be-62be6a76a1d0	POST	/api/v1/connector/sync/58f01ad7-88ad-4eb5-bf49-664a665185f7	44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a	202	{"status": "sync_triggered", "task_id": "e994b02e-3a18-4f92-a575-cbe74a40efa4", "estimated_duration_seconds": 30}	{}	2026-05-15 16:10:39.042825+00	2026-05-15 16:10:39.044498+00	2026-05-15 16:10:39.040998+00	2026-05-16 16:10:39.042826+00
b80161f6-70e7-4e0b-bc32-220b53d0be6e	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	eee94ab0-a5a3-4919-be4a-c4c615d38895	POST	/api/v1/connector/sync/58f01ad7-88ad-4eb5-bf49-664a665185f7	44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a	202	{"status": "sync_triggered", "task_id": "09f8ed19-390b-4a7f-8d9e-d77a6ec457c0", "estimated_duration_seconds": 30}	{}	2026-05-15 16:13:23.227311+00	2026-05-15 16:13:23.228714+00	2026-05-15 16:13:23.226225+00	2026-05-16 16:13:23.227313+00
29a849de-0502-4cf5-ab18-25dbecbb44e8	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	02020dd6-372b-48f8-9aad-b9c735b67b73	POST	/api/v1/connector/sync/58f01ad7-88ad-4eb5-bf49-664a665185f7	44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a	202	{"status": "sync_triggered", "task_id": "3282f257-14cd-4da6-a6f8-55bee5e7bb70", "estimated_duration_seconds": 30}	{}	2026-05-16 10:26:32.056592+00	2026-05-16 10:26:32.062725+00	2026-05-16 10:26:32.050369+00	2026-05-17 10:26:32.056596+00
3a75007a-6006-48a3-a9aa-771f83c9d660	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	1e150ad0-705e-4e8c-9ff9-bec68d4ddd46	POST	/api/v1/connector/sync/58f01ad7-88ad-4eb5-bf49-664a665185f7	44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a	202	{"status": "sync_triggered", "task_id": "44f64784-bfda-4667-b793-771189a8ca0a", "estimated_duration_seconds": 30}	{}	2026-05-16 10:59:14.79282+00	2026-05-16 10:59:14.793872+00	2026-05-16 10:59:14.791253+00	2026-05-17 10:59:14.792822+00
55f67fa3-e543-49c3-af0c-9824137137d2	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	6e4e15f3-ab55-41f5-b3f5-d8650a275915	POST	/api/v1/connector/sync/58f01ad7-88ad-4eb5-bf49-664a665185f7	44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a	202	{"status": "sync_triggered", "task_id": "a1450b03-28b9-45cd-98ec-c1d99e4cb0e5", "estimated_duration_seconds": 30}	{}	2026-05-16 11:01:29.262749+00	2026-05-16 11:01:29.263921+00	2026-05-16 11:01:29.261971+00	2026-05-17 11:01:29.26275+00
7c7fa379-5c8a-4dbc-a3c3-55964fc6475b	58f01ad7-88ad-4eb5-bf49-664a665185f7	79910b81-2a28-4fa4-8745-ee9d468a65bc	aa04b43a-cc1f-4056-8181-350d1d84b08e	POST	/api/v1/vouchers/	37fd6466f5acd6d6196a899ec77c39ee2b5dc5cfa547f5abb1879f1a5861cc7f	201	{"id": "7a7f4f81-8f96-4a2e-945e-e5146b567329", "cess": "0.00", "cgst": "0.00", "date": "2026-05-16", "igst": "0.00", "sgst": "0.00", "source": "manual", "status": "posted", "entries": [{"id": "f9eded1f-5ecd-4cab-9f6b-c928921de563", "cgst": null, "igst": null, "sgst": null, "amount": "1000.00", "gst_rate": null, "ledger_id": "de4f6c7c-d5b3-4c09-8a0b-98fe2beb71b7", "narration": null, "entry_type": "Dr", "tds_amount": null, "line_number": 1, "tds_section": null}, {"id": "b97909c3-051c-4d73-8399-fc0e5d1230e4", "cgst": null, "igst": null, "sgst": null, "amount": "1000.00", "gst_rate": null, "ledger_id": "35bc6d27-a185-4c02-bf8a-0c5c568e7068", "narration": null, "entry_type": "Cr", "tds_amount": null, "line_number": 2, "tds_section": null}], "narration": "Test voucher posted while Tally stopped", "reference": null, "company_id": "58f01ad7-88ad-4eb5-bf49-664a665185f7", "created_at": "2026-05-16T11:07:26.213469Z", "created_by": "79910b81-2a28-4fa4-8745-ee9d468a65bc", "tds_amount": "0.00", "tds_section": null, "total_amount": "1000.00", "voucher_type": "Payment", "gst_applicable": false, "is_auto_posted": false, "tds_applicable": false, "voucher_number": null, "place_of_supply": null, "tally_posted_at": null, "confidence_score": null, "is_optional_in_tally": false, "optional_rejected_at": null, "optional_rejected_by": null, "approved_to_regular_at": null, "approved_to_regular_by": null, "optional_rejection_reason": null}	{}	2026-05-16 11:07:26.215588+00	2026-05-16 11:07:26.238325+00	2026-05-16 11:07:26.213469+00	2026-05-17 11:07:26.215589+00
\.


--
-- Data for Name: ledger_entries; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.ledger_entries (id, company_id, voucher_id, ledger_id, amount, entry_type, line_number, narration, gst_rate, cgst, sgst, igst, tds_amount, tds_section, created_at) FROM stdin;
5910ca24-e637-42f4-947d-f168ce106fac	07eed054-9a72-40bf-89bc-592568fd1d26	6a113817-48c9-4f0e-a299-bb8cbb5e2386	74efb1df-8798-4da2-b16b-029163dec4dc	1500.99	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:25:43.231599+00
c27b76c8-ad9c-45d6-8b8d-903f2d47f8a5	07eed054-9a72-40bf-89bc-592568fd1d26	6a113817-48c9-4f0e-a299-bb8cbb5e2386	bbf818e8-a117-4eef-896c-f99c741e7736	1500.99	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:25:43.231599+00
3c0b5bf1-9611-499f-8176-9d002d4f9398	a8eea1e3-8697-4bdd-a735-e0d1222816d6	2e5f9e23-b025-47a9-8007-efba746dd2dd	30f7c49b-7ced-4625-a59f-2714d18eca80	1500.99	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:25:49.396815+00
77f5baa3-b5aa-4baa-9938-cb211984f63e	a8eea1e3-8697-4bdd-a735-e0d1222816d6	2e5f9e23-b025-47a9-8007-efba746dd2dd	c2a20c2a-f943-47b7-9e2d-f552f5712fdc	1500.99	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:25:49.396815+00
5d221d81-2085-4c36-a35c-29ddce893fbd	73c6c8f6-5d56-473a-92cc-88e036e6f515	4db81436-b985-49f7-97b3-17e3e3a19022	0ba833dd-2220-4be7-82d3-ff2dc592ca55	1500.99	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:25:52.117945+00
45e7e336-5524-4bb7-a406-d89aef1872c6	73c6c8f6-5d56-473a-92cc-88e036e6f515	4db81436-b985-49f7-97b3-17e3e3a19022	cab8fd0a-8c4d-4364-b024-fabec883e579	1500.99	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:25:52.117945+00
0facc024-286b-4606-8289-09501beed395	2b0bea11-d862-401f-b128-ec6e48cec60d	2afeee51-7b03-4d45-a4ef-54917c6d3964	c42a75a0-77bc-4eb7-950a-ef322a6713a2	1500.99	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:25:58.539514+00
0e57e5c6-269b-4d78-b298-851cef7cdf4e	2b0bea11-d862-401f-b128-ec6e48cec60d	2afeee51-7b03-4d45-a4ef-54917c6d3964	df3d3586-7f3f-4cba-9628-705650a69902	1500.99	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:25:58.539514+00
05facb5a-db0b-42be-a66b-406488a1840b	28819b28-ffd6-4877-b097-d68542903509	1a2c939b-7efd-4e6f-8ff1-cb15504883c0	f4d38d67-5532-4f4e-b4be-01afc7b06bbe	1500.99	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:03.756565+00
38b7f582-f618-40a3-85ea-71417c78f1ce	28819b28-ffd6-4877-b097-d68542903509	1a2c939b-7efd-4e6f-8ff1-cb15504883c0	9a71b5a6-fcae-40ff-ad15-5989c8fc160f	1500.99	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:03.756565+00
c4a26da2-57b1-4aa8-8788-a2263a99c4e6	fde328ce-64f3-4555-b472-d0a0f0ab7313	0f5b7e2a-6032-4765-ad1f-355b0c01919e	4163e6de-d7ad-42c6-8156-f41807a72e9c	1500.99	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:04.181062+00
e82883fd-9d9a-47d4-a95f-f7b846784e78	fde328ce-64f3-4555-b472-d0a0f0ab7313	0f5b7e2a-6032-4765-ad1f-355b0c01919e	c308bfc6-2bca-4bc9-ba96-0abaf2606a1e	1500.99	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:04.181062+00
f8a4a922-c13a-4e03-ba68-29821397fbd0	c9701980-a203-4995-81db-50eb351db7cd	2f7c9ed9-f337-413b-8119-5cd04343c1a1	42171f00-a457-4b71-9f44-710030c9b921	1500.99	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:06.814545+00
cae45298-f8c0-464f-8f6a-0b8b5817e716	c9701980-a203-4995-81db-50eb351db7cd	2f7c9ed9-f337-413b-8119-5cd04343c1a1	fa4d102a-ef67-4ce4-8922-58c6bf860a41	1500.99	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:06.814545+00
6e41d45b-a084-4cbc-b1bd-ca45d13c41fe	7ddbbff3-244f-42c3-b142-530b02e7f739	c5a3602f-5caa-4010-9354-162303de746d	adf6af3c-c451-4faf-9c68-b212aee4eaf8	1500.99	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:09.585838+00
777e1cac-fc53-4922-bd9b-47f1f8e54941	7ddbbff3-244f-42c3-b142-530b02e7f739	c5a3602f-5caa-4010-9354-162303de746d	71c0a0a6-afcf-48c2-ae41-ea4c1dc8c70e	1500.99	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:09.585838+00
50519e90-99eb-41e9-8759-9bdfe25c2b33	09214117-1d79-4d43-b074-028661f423bd	4c09ff5c-fc40-430e-900c-fdf3733e808b	704fe26c-4c5f-446b-80ef-9d959252c61a	1500.99	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:12.272876+00
72d14452-2825-4da0-891b-1ce57c73e494	09214117-1d79-4d43-b074-028661f423bd	4c09ff5c-fc40-430e-900c-fdf3733e808b	0e3c1f4e-04ce-42a3-8c68-6461f31d0727	1500.99	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:12.272876+00
698cdb9d-3c2e-4388-817b-4c40fbbf04d3	8ad00365-70bb-40bb-8f0c-e735597dcf86	10f24d67-945f-43e8-aca1-5ceed49397f5	3f38cd90-4c4b-4da7-9f4a-3294616f8704	1500.99	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:14.978365+00
542ea446-f0f2-477a-8a41-095ca87ed6bd	8ad00365-70bb-40bb-8f0c-e735597dcf86	10f24d67-945f-43e8-aca1-5ceed49397f5	332aa823-d64d-48fb-b908-4f4768621731	1500.99	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:14.978365+00
89ba2a82-e324-4aa9-89c0-55570d2c3546	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	374079a3-0845-4488-aeec-439ce8e056fc	84142669-e981-4380-ad1e-01ea3792d93a	1500.99	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:20.621447+00
de684b4f-376c-4207-b830-ae0889b75a22	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	374079a3-0845-4488-aeec-439ce8e056fc	86d3c0f6-f61d-4c8b-bcef-fa12c710a83d	1500.99	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:20.621447+00
ebf59508-a9eb-4ae7-b971-09da3a6bb0d9	249c8bde-f26e-4e5f-8caa-e5b1f476c86d	aa00e22a-bd24-4105-a89e-e51723c0e0bf	9ea79806-0a6f-4501-9db8-78df81083bfe	777.00	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:23.392231+00
692b3ba0-87d2-4d06-adff-33222e6b69b0	249c8bde-f26e-4e5f-8caa-e5b1f476c86d	aa00e22a-bd24-4105-a89e-e51723c0e0bf	a8773b36-b3b6-498a-ada6-6abd30726546	777.00	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:23.392231+00
f2dda572-be30-4b05-8ac3-c3f56e0c101b	b98ec1dc-23db-40d8-b9d6-e8e144da5f43	923a0f8b-974b-4f68-bf6f-f7ca16565910	98c9907a-c652-48f3-8806-b62320522340	777.00	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:26.029313+00
cb98cddd-5415-4b5a-bfbb-15ad8cabdeb8	b98ec1dc-23db-40d8-b9d6-e8e144da5f43	923a0f8b-974b-4f68-bf6f-f7ca16565910	3b200045-fd6f-40a2-8311-7224b5e59e0d	777.00	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:26.029313+00
9be1740d-a443-4e90-b973-71dcc32782bb	04a61e7b-797f-454b-911d-4c1948f93780	8b115e16-8f63-44cd-885a-0a1ca568b4a5	27b335dd-96c3-4645-8c8f-23d76be15f0b	100.00	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:28.687018+00
875079e0-5b96-4b74-924d-06de9aef90d3	04a61e7b-797f-454b-911d-4c1948f93780	8b115e16-8f63-44cd-885a-0a1ca568b4a5	89ce969e-d3df-4727-a43a-de0889169906	100.00	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:28.687018+00
d39b1d94-87b8-4cd4-b5da-53f17f7a1601	09b780ee-35f4-4d12-a8fc-ca07efd5bc00	171d2b75-0b40-4c2e-9b65-431d8718a23d	fda3ee6f-227d-43de-b7c3-92648f21cc05	777.00	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:33.948285+00
7403fcf6-e034-434c-b32e-2efad7d2da77	09b780ee-35f4-4d12-a8fc-ca07efd5bc00	171d2b75-0b40-4c2e-9b65-431d8718a23d	584fa4f5-4bc1-482e-a58b-2d06aeb22ea5	777.00	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:33.948285+00
17d943ee-0118-48fe-8c2d-c3a045c47de7	74c493c5-e0ab-4802-9b5a-b64749462c43	5a4a30ee-4b65-49c6-8d82-426012f026ed	b7df9206-c941-45e0-8bc1-597f87cc500b	777.00	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:36.615238+00
76481e65-903f-4359-8166-ebae705fa021	74c493c5-e0ab-4802-9b5a-b64749462c43	5a4a30ee-4b65-49c6-8d82-426012f026ed	76ded8fa-df0c-44bc-a470-c3ecc7dd3b4f	777.00	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-13 13:26:36.615238+00
f9eded1f-5ecd-4cab-9f6b-c928921de563	58f01ad7-88ad-4eb5-bf49-664a665185f7	7a7f4f81-8f96-4a2e-945e-e5146b567329	de4f6c7c-d5b3-4c09-8a0b-98fe2beb71b7	1000.00	Dr	1	\N	\N	\N	\N	\N	\N	\N	2026-05-16 11:07:26.213469+00
b97909c3-051c-4d73-8399-fc0e5d1230e4	58f01ad7-88ad-4eb5-bf49-664a665185f7	7a7f4f81-8f96-4a2e-945e-e5146b567329	35bc6d27-a185-4c02-bf8a-0c5c568e7068	1000.00	Cr	2	\N	\N	\N	\N	\N	\N	\N	2026-05-16 11:07:26.213469+00
\.


--
-- Data for Name: ledgers; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.ledgers (id, company_id, name, name_normalized, group_name, parent_ledger_id, opening_balance, balance_type, gstin, pan, phone, email, address, state_code, is_active, tally_master_id, tally_synced_at, created_at, updated_at) FROM stdin;
b11e18fb-e3e3-44ec-8783-d264f2c679ce	e6ad6664-9dd8-4a58-93cb-da56e4cbd3aa	Bank-5d1a	bank-5d1a	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:40.430305+00	2026-05-13 13:25:40.430305+00
abd8f149-65db-499f-ade1-7b28f364271e	e6ad6664-9dd8-4a58-93cb-da56e4cbd3aa	Party-5d5f	party-5d5f	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:40.46602+00	2026-05-13 13:25:40.46602+00
74efb1df-8798-4da2-b16b-029163dec4dc	07eed054-9a72-40bf-89bc-592568fd1d26	Bank-db0a	bank-db0a	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:43.206485+00	2026-05-13 13:25:43.206485+00
bbf818e8-a117-4eef-896c-f99c741e7736	07eed054-9a72-40bf-89bc-592568fd1d26	Party-34f4	party-34f4	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:43.218192+00	2026-05-13 13:25:43.218192+00
0218efa5-ec91-4ef2-b4cb-7a3b631f0ff3	69e53193-f10f-48d1-880f-e4745b39ed47	Bank-082a	bank-082a	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:46.49164+00	2026-05-13 13:25:46.49164+00
d0556e2f-f366-4e4b-a9c6-78c58cf09286	69e53193-f10f-48d1-880f-e4745b39ed47	Party-0796	party-0796	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:46.504898+00	2026-05-13 13:25:46.504898+00
30f7c49b-7ced-4625-a59f-2714d18eca80	a8eea1e3-8697-4bdd-a735-e0d1222816d6	Bank-89fd	bank-89fd	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:49.277631+00	2026-05-13 13:25:49.277631+00
c2a20c2a-f943-47b7-9e2d-f552f5712fdc	a8eea1e3-8697-4bdd-a735-e0d1222816d6	Party-f677	party-f677	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:49.292233+00	2026-05-13 13:25:49.292233+00
0ba833dd-2220-4be7-82d3-ff2dc592ca55	73c6c8f6-5d56-473a-92cc-88e036e6f515	Bank-3fdd	bank-3fdd	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:52.087405+00	2026-05-13 13:25:52.087405+00
cab8fd0a-8c4d-4364-b024-fabec883e579	73c6c8f6-5d56-473a-92cc-88e036e6f515	Party-e5ad	party-e5ad	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:52.103203+00	2026-05-13 13:25:52.103203+00
1d4febcb-3c9f-4069-a36e-02996d5b1e19	d73af30c-8808-471d-83ca-553d0163247f	Bank-bbce	bank-bbce	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:55.086816+00	2026-05-13 13:25:55.086816+00
6026bc11-9a1b-49bb-8bec-70174bf5138b	d73af30c-8808-471d-83ca-553d0163247f	Party-629a	party-629a	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:55.098663+00	2026-05-13 13:25:55.098663+00
c42a75a0-77bc-4eb7-950a-ef322a6713a2	2b0bea11-d862-401f-b128-ec6e48cec60d	Bank-96a2	bank-96a2	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:58.103885+00	2026-05-13 13:25:58.103885+00
df3d3586-7f3f-4cba-9628-705650a69902	2b0bea11-d862-401f-b128-ec6e48cec60d	Party-d012	party-d012	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:25:58.123523+00	2026-05-13 13:25:58.123523+00
61be9fec-5338-485e-b524-57a768a54cde	60ca15f3-0e5c-432b-8b0d-a77d783129b3	Bank-6704	bank-6704	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:01.114493+00	2026-05-13 13:26:01.114493+00
80f8f35e-aeb5-4ae9-9c4c-5c904e1df46f	60ca15f3-0e5c-432b-8b0d-a77d783129b3	Party-0aae	party-0aae	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:01.126741+00	2026-05-13 13:26:01.126741+00
f4d38d67-5532-4f4e-b4be-01afc7b06bbe	28819b28-ffd6-4877-b097-d68542903509	Bank-1377	bank-1377	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:03.728119+00	2026-05-13 13:26:03.728119+00
9a71b5a6-fcae-40ff-ad15-5989c8fc160f	28819b28-ffd6-4877-b097-d68542903509	Party-6129	party-6129	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:03.743895+00	2026-05-13 13:26:03.743895+00
4163e6de-d7ad-42c6-8156-f41807a72e9c	fde328ce-64f3-4555-b472-d0a0f0ab7313	Bank-B-dd7b	bank-b-dd7b	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:04.151521+00	2026-05-13 13:26:04.151521+00
c308bfc6-2bca-4bc9-ba96-0abaf2606a1e	fde328ce-64f3-4555-b472-d0a0f0ab7313	Party-B-b7a3	party-b-b7a3	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:04.166585+00	2026-05-13 13:26:04.166585+00
42171f00-a457-4b71-9f44-710030c9b921	c9701980-a203-4995-81db-50eb351db7cd	Bank-0ad6	bank-0ad6	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:06.788361+00	2026-05-13 13:26:06.788361+00
fa4d102a-ef67-4ce4-8922-58c6bf860a41	c9701980-a203-4995-81db-50eb351db7cd	Party-a02e	party-a02e	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:06.800682+00	2026-05-13 13:26:06.800682+00
adf6af3c-c451-4faf-9c68-b212aee4eaf8	7ddbbff3-244f-42c3-b142-530b02e7f739	Bank-71d8	bank-71d8	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:09.546055+00	2026-05-13 13:26:09.546055+00
71c0a0a6-afcf-48c2-ae41-ea4c1dc8c70e	7ddbbff3-244f-42c3-b142-530b02e7f739	Party-a4c4	party-a4c4	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:09.56803+00	2026-05-13 13:26:09.56803+00
704fe26c-4c5f-446b-80ef-9d959252c61a	09214117-1d79-4d43-b074-028661f423bd	Bank-9c54	bank-9c54	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:12.244296+00	2026-05-13 13:26:12.244296+00
0e3c1f4e-04ce-42a3-8c68-6461f31d0727	09214117-1d79-4d43-b074-028661f423bd	Party-3dc1	party-3dc1	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:12.258175+00	2026-05-13 13:26:12.258175+00
3f38cd90-4c4b-4da7-9f4a-3294616f8704	8ad00365-70bb-40bb-8f0c-e735597dcf86	Bank-e280	bank-e280	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:14.931873+00	2026-05-13 13:26:14.931873+00
332aa823-d64d-48fb-b908-4f4768621731	8ad00365-70bb-40bb-8f0c-e735597dcf86	Party-5ca3	party-5ca3	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:14.948288+00	2026-05-13 13:26:14.948288+00
61bf2ce0-a293-45ae-9d8f-5a84230a8457	9f6ced8d-775b-4061-bde5-7098fc05112a	Bank-bd37	bank-bd37	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:17.609991+00	2026-05-13 13:26:17.609991+00
70dac9ed-76a0-4d29-a997-1bb475e461bb	9f6ced8d-775b-4061-bde5-7098fc05112a	Party-3415	party-3415	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:17.622321+00	2026-05-13 13:26:17.622321+00
84142669-e981-4380-ad1e-01ea3792d93a	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	Bank-8795	bank-8795	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:20.594603+00	2026-05-13 13:26:20.594603+00
86d3c0f6-f61d-4c8b-bcef-fa12c710a83d	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	Party-ce7d	party-ce7d	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:20.608336+00	2026-05-13 13:26:20.608336+00
9ea79806-0a6f-4501-9db8-78df81083bfe	249c8bde-f26e-4e5f-8caa-e5b1f476c86d	Bank-4422	bank-4422	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:23.36558+00	2026-05-13 13:26:23.36558+00
a8773b36-b3b6-498a-ada6-6abd30726546	249c8bde-f26e-4e5f-8caa-e5b1f476c86d	Party-28d6	party-28d6	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:23.377214+00	2026-05-13 13:26:23.377214+00
98c9907a-c652-48f3-8806-b62320522340	b98ec1dc-23db-40d8-b9d6-e8e144da5f43	Bank-4f29	bank-4f29	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:26.002636+00	2026-05-13 13:26:26.002636+00
3b200045-fd6f-40a2-8311-7224b5e59e0d	b98ec1dc-23db-40d8-b9d6-e8e144da5f43	Party-cdd6	party-cdd6	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:26.016304+00	2026-05-13 13:26:26.016304+00
27b335dd-96c3-4645-8c8f-23d76be15f0b	04a61e7b-797f-454b-911d-4c1948f93780	Bank-c455	bank-c455	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:28.657587+00	2026-05-13 13:26:28.657587+00
89ce969e-d3df-4727-a43a-de0889169906	04a61e7b-797f-454b-911d-4c1948f93780	Party-a135	party-a135	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:28.671197+00	2026-05-13 13:26:28.671197+00
36f52252-f968-4584-b082-395f76bf01d1	c343c25d-cb4d-4461-8366-d18a7a433534	Bank-0fcb	bank-0fcb	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:31.296903+00	2026-05-13 13:26:31.296903+00
47fdf2b9-ec52-471a-9e1f-486f38480495	c343c25d-cb4d-4461-8366-d18a7a433534	Party-b435	party-b435	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:31.312656+00	2026-05-13 13:26:31.312656+00
fda3ee6f-227d-43de-b7c3-92648f21cc05	09b780ee-35f4-4d12-a8fc-ca07efd5bc00	Bank-ae22	bank-ae22	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:33.920367+00	2026-05-13 13:26:33.920367+00
584fa4f5-4bc1-482e-a58b-2d06aeb22ea5	09b780ee-35f4-4d12-a8fc-ca07efd5bc00	Party-d412	party-d412	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:33.934705+00	2026-05-13 13:26:33.934705+00
b7df9206-c941-45e0-8bc1-597f87cc500b	74c493c5-e0ab-4802-9b5a-b64749462c43	Bank-e50a	bank-e50a	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:36.558766+00	2026-05-13 13:26:36.558766+00
76ded8fa-df0c-44bc-a470-c3ecc7dd3b4f	74c493c5-e0ab-4802-9b5a-b64749462c43	Party-8dfd	party-8dfd	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-13 13:26:36.573384+00	2026-05-13 13:26:36.573384+00
de4f6c7c-d5b3-4c09-8a0b-98fe2beb71b7	58f01ad7-88ad-4eb5-bf49-664a665185f7	ABC LTD	abc ltd	Sundry Creditors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-16 10:59:15.285235+00	2026-05-16 10:59:15.285235+00
35bc6d27-a185-4c02-bf8a-0c5c568e7068	58f01ad7-88ad-4eb5-bf49-664a665185f7	Cash	cash	Cash-in-Hand	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-16 10:59:15.285235+00	2026-05-16 10:59:15.285235+00
47b8d9d4-9797-4539-955e-6a38d41a7b70	58f01ad7-88ad-4eb5-bf49-664a665185f7	HDFC BANK	hdfc bank	Bank Accounts	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-16 10:59:15.285235+00	2026-05-16 10:59:15.285235+00
fc5ffae9-50eb-499e-a534-694b0978745e	58f01ad7-88ad-4eb5-bf49-664a665185f7	Profit & Loss A/c	profit & loss a/c	Primary	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-16 10:59:15.285235+00	2026-05-16 10:59:15.285235+00
8d919437-2a12-43ba-bb80-ac30459ae4b7	58f01ad7-88ad-4eb5-bf49-664a665185f7	Xyz Ltd	xyz ltd	Sundry Debtors	\N	0.00	Dr	\N	\N	\N	\N	\N	\N	t	\N	\N	2026-05-16 10:59:15.285235+00	2026-05-16 10:59:15.285235+00
\.


--
-- Data for Name: user_companies; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_companies (id, user_id, company_id, role, created_at, updated_at) FROM stdin;
a9bb2a40-58b5-497f-944d-6cf00996c46b	ec471c79-a85d-4873-bd8d-ee08da19707e	e6ad6664-9dd8-4a58-93cb-da56e4cbd3aa	owner	2026-05-13 13:25:40.382895+00	2026-05-13 13:25:40.382895+00
6d74a3d7-ab63-42e0-b8a0-653a6e862f4b	85cda290-e587-4637-bc95-032c5fadfe1e	07eed054-9a72-40bf-89bc-592568fd1d26	owner	2026-05-13 13:25:43.192839+00	2026-05-13 13:25:43.192839+00
7e8252df-3f5d-491c-9cc4-6d6090cdbbf8	05256d5b-fcc6-4c2c-a991-fd36d23d6504	69e53193-f10f-48d1-880f-e4745b39ed47	owner	2026-05-13 13:25:46.475853+00	2026-05-13 13:25:46.475853+00
0dd7e962-6f3c-43ce-a16b-76e122370b2c	f7eaecff-8db0-466b-a734-74294dca8654	a8eea1e3-8697-4bdd-a735-e0d1222816d6	owner	2026-05-13 13:25:49.261053+00	2026-05-13 13:25:49.261053+00
a289cec7-9b6e-43ee-bf0a-e5ac4d2d8ee1	abfdd8f5-32d5-4358-933a-c786fb875466	73c6c8f6-5d56-473a-92cc-88e036e6f515	owner	2026-05-13 13:25:52.073136+00	2026-05-13 13:25:52.073136+00
ee3c70e3-e02c-4f86-b189-534db0722e0d	08d3d460-f4d5-45c2-96c5-7d2f4e85e2b5	908c5157-d04e-40e6-b01e-e3ba44912beb	owner	2026-05-13 13:25:52.505195+00	2026-05-13 13:25:52.505195+00
fcd76d48-6f44-41f2-9c0a-4d89d2bc5015	df5b8c8b-2083-40fb-8815-e3b289b88f81	d73af30c-8808-471d-83ca-553d0163247f	owner	2026-05-13 13:25:55.073358+00	2026-05-13 13:25:55.073358+00
bf88a9c3-7345-4b3d-ad6d-285718343975	19b0ceeb-b97f-4ba4-9d13-6017f516d171	b6b8b865-f868-485b-9a5d-74964a413d45	owner	2026-05-13 13:25:55.466148+00	2026-05-13 13:25:55.466148+00
fd32cc8e-1042-46ca-aa6c-8ea69be5b20d	5f0759b7-260a-4a7a-a61e-c113939f9206	2b0bea11-d862-401f-b128-ec6e48cec60d	owner	2026-05-13 13:25:58.061536+00	2026-05-13 13:25:58.061536+00
2ec9ce8c-cbeb-4ab6-ad05-cdf4f4557835	a60dfcad-bf58-49c4-9435-fc9b1de6523c	ebbf6003-37d5-4265-9184-8fdfaba5793a	owner	2026-05-13 13:25:58.524947+00	2026-05-13 13:25:58.524947+00
5955f9e5-f089-49f0-82bb-6ad6c63ad7d4	d072f2df-465f-4aaf-bbbf-663ccbe52dbe	60ca15f3-0e5c-432b-8b0d-a77d783129b3	owner	2026-05-13 13:26:01.096768+00	2026-05-13 13:26:01.096768+00
8feee3ad-3b51-4eec-a74c-c1a8965ddf0a	8d40beaf-87e9-47f6-8f8f-68e350011e76	28819b28-ffd6-4877-b097-d68542903509	owner	2026-05-13 13:26:03.708057+00	2026-05-13 13:26:03.708057+00
e6b66ab1-8940-4a73-b954-027b362da542	e53e23ba-5809-4965-a6e2-129f3c7682f1	fde328ce-64f3-4555-b472-d0a0f0ab7313	owner	2026-05-13 13:26:04.133318+00	2026-05-13 13:26:04.133318+00
25ac7f2e-393e-4123-ade9-5099d2a6f5c4	781417d6-2f31-4388-a3d1-19ea97d14754	c9701980-a203-4995-81db-50eb351db7cd	owner	2026-05-13 13:26:06.774564+00	2026-05-13 13:26:06.774564+00
1766bb5b-ed2b-4bad-bde2-e2a58aaf4ad9	beccb035-b15f-4e8f-98dc-bba5381522c2	7ddbbff3-244f-42c3-b142-530b02e7f739	owner	2026-05-13 13:26:09.52884+00	2026-05-13 13:26:09.52884+00
523afb28-8db0-4fd4-b3d5-1d9a6946f133	00b34604-0adb-4950-b38c-25b0d81424f6	09214117-1d79-4d43-b074-028661f423bd	owner	2026-05-13 13:26:12.229119+00	2026-05-13 13:26:12.229119+00
45a9850e-fdee-488e-937e-3c20516649cb	0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2	8ad00365-70bb-40bb-8f0c-e735597dcf86	owner	2026-05-13 13:26:14.914642+00	2026-05-13 13:26:14.914642+00
fb6b85cc-b682-45db-b9a5-b74923d99526	aaddd93f-258f-46d1-8e79-6c91ca0c6e6f	9f6ced8d-775b-4061-bde5-7098fc05112a	owner	2026-05-13 13:26:17.595625+00	2026-05-13 13:26:17.595625+00
dc9d6156-fab3-418d-af7e-a7b18adf9a4d	1e4f1313-810c-4b42-9be9-2ea4597a1a21	9f6ced8d-775b-4061-bde5-7098fc05112a	viewer	2026-05-13 13:26:17.992492+00	2026-05-13 13:26:17.992492+00
654ca749-a657-4643-a3c0-46942caea33c	e5e8c459-79f3-4ac7-b2db-cee90c388ba8	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	owner	2026-05-13 13:26:20.579889+00	2026-05-13 13:26:20.579889+00
1f6b5847-d65b-4868-903e-4b366d73ae50	1b75c626-594e-4a15-87a3-0e6ad41958d1	249c8bde-f26e-4e5f-8caa-e5b1f476c86d	owner	2026-05-13 13:26:23.351868+00	2026-05-13 13:26:23.351868+00
53e93237-12af-4d22-805c-801e62bc7c05	956f8b68-30a0-42ee-9f8f-d4e52037c458	b98ec1dc-23db-40d8-b9d6-e8e144da5f43	owner	2026-05-13 13:26:25.989171+00	2026-05-13 13:26:25.989171+00
dd06d4c2-b1a1-4d51-b7b4-63940a0a24f0	fb92feda-96f4-412e-a999-a68fcd0bde1c	04a61e7b-797f-454b-911d-4c1948f93780	owner	2026-05-13 13:26:28.64231+00	2026-05-13 13:26:28.64231+00
40d01f7f-bc1c-43d6-84ab-a95c244a3538	2d78c851-fe2c-4ab9-83e8-5d5f511a67de	c343c25d-cb4d-4461-8366-d18a7a433534	owner	2026-05-13 13:26:31.283354+00	2026-05-13 13:26:31.283354+00
d2b7a88f-4013-4dfc-9196-9f9750afeff5	27d19365-6c20-4347-9f87-724c94616e89	09b780ee-35f4-4d12-a8fc-ca07efd5bc00	owner	2026-05-13 13:26:33.90681+00	2026-05-13 13:26:33.90681+00
eb3c91d2-e25b-4daf-b656-a4d69bf2af64	57daf9e8-9922-4cc0-9710-fa70b1fd51dd	74c493c5-e0ab-4802-9b5a-b64749462c43	owner	2026-05-13 13:26:36.537433+00	2026-05-13 13:26:36.537433+00
8c75d5ce-75ea-4161-a387-29ac791ec4e6	79910b81-2a28-4fa4-8745-ee9d468a65bc	58f01ad7-88ad-4eb5-bf49-664a665185f7	owner	2026-05-14 10:35:44.764267+00	2026-05-14 10:35:44.764267+00
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.users (id, email, hashed_password, full_name, phone, is_ca, firm_name, ca_membership_no, is_active, is_superuser, last_login_at, created_at, updated_at) FROM stdin;
ec471c79-a85d-4873-bd8d-ee08da19707e	v7user-453b25aafc@example.com	$2b$12$Qizn6uqzU6F5Idqqmy.7c.oi.49lcU.V2bvwzryuFEv9qQnpkOUcy	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:25:40.360916+00	2026-05-13 13:25:40.006946+00	2026-05-13 13:25:40.188241+00
85cda290-e587-4637-bc95-032c5fadfe1e	v7user-650bc45132@example.com	$2b$12$bIlDCvWC09s5HgtYwnM/3OLjwhw8ZeZdKt7IRhj.pv9FZnGxxNZsq	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:25:43.182157+00	2026-05-13 13:25:42.817325+00	2026-05-13 13:25:43.008379+00
05256d5b-fcc6-4c2c-a991-fd36d23d6504	v7user-439faf3c9a@example.com	$2b$12$ylnZSbPHo0mQnLW7huWoru9bHGGLlCyr04F25gjGOQvTqHp623AVm	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:25:46.464184+00	2026-05-13 13:25:46.100334+00	2026-05-13 13:25:46.290962+00
f7eaecff-8db0-466b-a734-74294dca8654	v7user-3dd6144bf7@example.com	$2b$12$OWeWgw5UurBvzyXjd01YZObnkzgLAckOZKA4Jw5ue9wGO6WUkIspu	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:25:49.249781+00	2026-05-13 13:25:48.858087+00	2026-05-13 13:25:49.071541+00
abfdd8f5-32d5-4358-933a-c786fb875466	v7user-db18bf9530@example.com	$2b$12$xsP8L757ssRO9OEGM.WebOzB.WB5yjfmtLZtqQqBimXNmVx2ki63W	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:25:52.062944+00	2026-05-13 13:25:51.710785+00	2026-05-13 13:25:51.88869+00
08d3d460-f4d5-45c2-96c5-7d2f4e85e2b5	v7user-265fcbf3f9@example.com	$2b$12$71nJYffcaGuNiroaMGgpiu7a0pKmhOgPuJnD5nDHVr130LMPW2XLy	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:25:52.494747+00	2026-05-13 13:25:52.137662+00	2026-05-13 13:25:52.324176+00
df5b8c8b-2083-40fb-8815-e3b289b88f81	v7user-49da1e7711@example.com	$2b$12$NMdI9v9B0F5jyhlTvFlUAOrROT2lN9TknXKu7jOAoqZkyZCcpiHnO	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:25:55.061685+00	2026-05-13 13:25:54.71153+00	2026-05-13 13:25:54.892587+00
19b0ceeb-b97f-4ba4-9d13-6017f516d171	v7user-7f24290c0f@example.com	$2b$12$cebwO2dAfQrTjrcUa7tk9.dnTQfAVRV8jFdUw0BQHGcXbjPdeZWVG	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:25:55.454567+00	2026-05-13 13:25:55.108423+00	2026-05-13 13:25:55.286735+00
5f0759b7-260a-4a7a-a61e-c113939f9206	v7user-76a8a81659@example.com	$2b$12$fq23ZFL8tXhnqN47Upzu7eZNs4/eznaqRuwiwC9e4Wkq/Qy8NHePC	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:25:58.044316+00	2026-05-13 13:25:57.683238+00	2026-05-13 13:25:57.865562+00
a60dfcad-bf58-49c4-9435-fc9b1de6523c	v7user-4cdce9faff@example.com	$2b$12$lj3VEARSuhSbkt75VR1RY.Mayp2.70Q0Q0G6INeZlyyXyyXJXegH2	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:25:58.512627+00	2026-05-13 13:25:58.135276+00	2026-05-13 13:25:58.342132+00
d072f2df-465f-4aaf-bbbf-663ccbe52dbe	v7user-4a83f867a2@example.com	$2b$12$C5Dul7NYhNyfahCyBcSlPe9bg0IeDvcjJrydCWs5/Ah48fwQTCroi	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:01.083828+00	2026-05-13 13:26:00.736176+00	2026-05-13 13:26:00.914548+00
8d40beaf-87e9-47f6-8f8f-68e350011e76	v7user-05c16b068f@example.com	$2b$12$HGYIYIunBWwtRDzIDZK/2eYlYlNHSgkf9qGB3Nh8SqL5/xNqvcAqa	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:03.696691+00	2026-05-13 13:26:03.351646+00	2026-05-13 13:26:03.529553+00
e53e23ba-5809-4965-a6e2-129f3c7682f1	v7user-e677de16f8@example.com	$2b$12$lFS/PNxswN3OisH.QyBYo.Lyrw7Kc.mwd0276m.IDRJaSKCn5gys.	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:04.121995+00	2026-05-13 13:26:03.775135+00	2026-05-13 13:26:03.953123+00
781417d6-2f31-4388-a3d1-19ea97d14754	v7user-58c9e830c7@example.com	$2b$12$N6wv.ER41ZtwalJALRr/6OD7SJmNUv7yzEAxotZPJ7sUjmiExR.Mm	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:06.762256+00	2026-05-13 13:26:06.414237+00	2026-05-13 13:26:06.593275+00
beccb035-b15f-4e8f-98dc-bba5381522c2	v7user-d9c7d58255@example.com	$2b$12$bhV7WiIBOjn/kt/ADq4T3.icAq0YaipJRT2YYMUEr0rrwXvXan18G	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:09.517209+00	2026-05-13 13:26:09.170645+00	2026-05-13 13:26:09.348608+00
00b34604-0adb-4950-b38c-25b0d81424f6	v7user-f0b6eb39a4@example.com	$2b$12$d18HYr2UVPwaOE6zRLfUh.bdr5l/dDh.fkkqwf8.DtU9VA1PGwfKy	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:12.217853+00	2026-05-13 13:26:11.857965+00	2026-05-13 13:26:12.043516+00
0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2	v7user-10d9a3aa2e@example.com	$2b$12$3UwAHS8r.Nlu6a8NIpgObOX8MOtQHyDYJk12sPovvAfh5xmA/Tef2	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:14.903545+00	2026-05-13 13:26:14.553507+00	2026-05-13 13:26:14.731932+00
aaddd93f-258f-46d1-8e79-6c91ca0c6e6f	v7user-5e2768fd14@example.com	$2b$12$5YwJvGxGcMtfzNOTOqNgx.W25Buk27TSO12ye2wdjINbWXAWDhCwC	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:17.585099+00	2026-05-13 13:26:17.232321+00	2026-05-13 13:26:17.413297+00
1e4f1313-810c-4b42-9be9-2ea4597a1a21	v7user-70cd338ac9@example.com	$2b$12$8RMPDbJ4jMpYZ09gEryMd.yC3RWryrlIsx4zWOfhX0c6aCfk.ZNLq	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:17.982145+00	2026-05-13 13:26:17.630197+00	2026-05-13 13:26:17.81114+00
e5e8c459-79f3-4ac7-b2db-cee90c388ba8	v7user-d97ad9f9b7@example.com	$2b$12$6nb6N.mp7TVxktHxlvY..e2C6dyG6Hn7RNkBfYzI8pKpcF23CetGS	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:20.56975+00	2026-05-13 13:26:20.220212+00	2026-05-13 13:26:20.40045+00
1b75c626-594e-4a15-87a3-0e6ad41958d1	v7user-5cfad7384c@example.com	$2b$12$YO3lzFtKLFAehM7k.nccmu6qwxPDXfkLftHW2TXJ2/yrr5iC7MgWa	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:23.340901+00	2026-05-13 13:26:22.984523+00	2026-05-13 13:26:23.165145+00
956f8b68-30a0-42ee-9f8f-d4e52037c458	v7user-175b47f10f@example.com	$2b$12$H7yBvadYyInntLo33XaIgupcGCPedp2LW5xCtuRFH9O.VXcvlP5eu	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:25.978461+00	2026-05-13 13:26:25.626245+00	2026-05-13 13:26:25.808756+00
fb92feda-96f4-412e-a999-a68fcd0bde1c	v7user-628e0ac41c@example.com	$2b$12$cpNuZpz7WGTS37PW6wWj2Ojfc.uZGb4RqZ1mJF0Ub9Z8dx1WuTFpy	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:28.630723+00	2026-05-13 13:26:28.278898+00	2026-05-13 13:26:28.459594+00
2d78c851-fe2c-4ab9-83e8-5d5f511a67de	v7user-5ce7d7ab8d@example.com	$2b$12$9i1Cqa/5gzplP0CcWjhCieWYJPe.A7d2JCKfw4PpF0RYlOgHbsP3O	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:31.272257+00	2026-05-13 13:26:30.917812+00	2026-05-13 13:26:31.09996+00
27d19365-6c20-4347-9f87-724c94616e89	v7user-94f67365ac@example.com	$2b$12$JfLucMKTy6uyZsU7/h6XDOZ1v6WwSfrxNTkTb/mVJYJvPp2669Vpy	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:33.895168+00	2026-05-13 13:26:33.5462+00	2026-05-13 13:26:33.724952+00
57daf9e8-9922-4cc0-9710-fa70b1fd51dd	v7user-44ca6e5b76@example.com	$2b$12$dEbVPUmCbf0GxE83TzwAa.WqxhzksxD.u.D57O06HMkmJoZjEAONW	Validation User	\N	f	\N	\N	t	f	2026-05-13 13:26:36.524258+00	2026-05-13 13:26:36.159696+00	2026-05-13 13:26:36.349246+00
79910b81-2a28-4fa4-8745-ee9d468a65bc	test@taxmindbooks.dev	$2b$12$VTDBJvt54Qx8iXoAMrDXKenqHvQJJf/j1ORgTKLHBnsU3ySRBhDWW	Test User	+919999999999	f	\N	\N	t	f	2026-05-16 11:05:19.649707+00	2026-05-13 13:12:46.740129+00	2026-05-16 11:05:19.488485+00
\.


--
-- Data for Name: vouchers; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.vouchers (id, company_id, voucher_type, voucher_number, date, narration, reference, total_amount, status, source, source_ingestion_id, is_auto_posted, confidence_score, gst_applicable, place_of_supply, cgst, sgst, igst, cess, tds_applicable, tds_amount, tds_section, tally_posted_at, tally_voucher_guid, tally_post_attempts, tally_last_error, created_by, approved_by, approved_at, created_at, updated_at, is_optional_in_tally, approved_to_regular_at, approved_to_regular_by, optional_rejection_reason, optional_rejected_at, optional_rejected_by) FROM stdin;
6a113817-48c9-4f0e-a299-bb8cbb5e2386	07eed054-9a72-40bf-89bc-592568fd1d26	Receipt	\N	2026-05-08	Validation suite test voucher	\N	1500.99	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	85cda290-e587-4637-bc95-032c5fadfe1e	\N	\N	2026-05-13 13:25:43.231599+00	2026-05-13 13:25:43.231599+00	f	\N	\N	\N	\N	\N
2e5f9e23-b025-47a9-8007-efba746dd2dd	a8eea1e3-8697-4bdd-a735-e0d1222816d6	Receipt	\N	2026-05-08	Validation suite test voucher	\N	1500.99	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	f7eaecff-8db0-466b-a734-74294dca8654	\N	\N	2026-05-13 13:25:49.396815+00	2026-05-13 13:25:49.396815+00	f	\N	\N	\N	\N	\N
4db81436-b985-49f7-97b3-17e3e3a19022	73c6c8f6-5d56-473a-92cc-88e036e6f515	Receipt	\N	2026-05-08	Validation suite test voucher	\N	1500.99	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	abfdd8f5-32d5-4358-933a-c786fb875466	\N	\N	2026-05-13 13:25:52.117945+00	2026-05-13 13:25:52.117945+00	f	\N	\N	\N	\N	\N
2afeee51-7b03-4d45-a4ef-54917c6d3964	2b0bea11-d862-401f-b128-ec6e48cec60d	Receipt	\N	2026-05-08	Validation suite test voucher	\N	1500.99	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	5f0759b7-260a-4a7a-a61e-c113939f9206	\N	\N	2026-05-13 13:25:58.539514+00	2026-05-13 13:25:58.539514+00	f	\N	\N	\N	\N	\N
1a2c939b-7efd-4e6f-8ff1-cb15504883c0	28819b28-ffd6-4877-b097-d68542903509	Receipt	\N	2026-05-08	company-A row	\N	1500.99	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	8d40beaf-87e9-47f6-8f8f-68e350011e76	\N	\N	2026-05-13 13:26:03.756565+00	2026-05-13 13:26:03.756565+00	f	\N	\N	\N	\N	\N
0f5b7e2a-6032-4765-ad1f-355b0c01919e	fde328ce-64f3-4555-b472-d0a0f0ab7313	Receipt	\N	2026-05-08	company-B row	\N	1500.99	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	e53e23ba-5809-4965-a6e2-129f3c7682f1	\N	\N	2026-05-13 13:26:04.181062+00	2026-05-13 13:26:04.181062+00	f	\N	\N	\N	\N	\N
2f7c9ed9-f337-413b-8119-5cd04343c1a1	c9701980-a203-4995-81db-50eb351db7cd	Receipt	\N	2026-05-08	Validation suite test voucher	\N	1500.99	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	781417d6-2f31-4388-a3d1-19ea97d14754	\N	\N	2026-05-13 13:26:06.814545+00	2026-05-13 13:26:06.814545+00	f	\N	\N	\N	\N	\N
c5a3602f-5caa-4010-9354-162303de746d	7ddbbff3-244f-42c3-b142-530b02e7f739	Receipt	\N	2026-05-08	updated narration	\N	1500.99	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	beccb035-b15f-4e8f-98dc-bba5381522c2	\N	\N	2026-05-13 13:26:09.585838+00	2026-05-13 13:26:09.61037+00	f	\N	\N	\N	\N	\N
4c09ff5c-fc40-430e-900c-fdf3733e808b	09214117-1d79-4d43-b074-028661f423bd	Receipt	\N	2026-05-08	pre-cancel update	\N	1500.99	cancelled	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	00b34604-0adb-4950-b38c-25b0d81424f6	\N	\N	2026-05-13 13:26:12.272876+00	2026-05-13 13:26:12.317011+00	f	\N	\N	\N	\N	\N
10f24d67-945f-43e8-aca1-5ceed49397f5	8ad00365-70bb-40bb-8f0c-e735597dcf86	Receipt	\N	2026-05-08	Validation suite test voucher	\N	1500.99	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	0214e8e6-a096-4ee2-99bc-e1d2ae53bcc2	\N	\N	2026-05-13 13:26:14.978365+00	2026-05-13 13:26:14.978365+00	f	\N	\N	\N	\N	\N
374079a3-0845-4488-aeec-439ce8e056fc	6f1e7c99-48ec-42ec-8a78-111a5dd0f0af	Receipt	\N	2026-05-08	redaction-probe-606614	\N	1500.99	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	e5e8c459-79f3-4ac7-b2db-cee90c388ba8	\N	\N	2026-05-13 13:26:20.621447+00	2026-05-13 13:26:20.644889+00	f	\N	\N	\N	\N	\N
aa00e22a-bd24-4105-a89e-e51723c0e0bf	249c8bde-f26e-4e5f-8caa-e5b1f476c86d	Receipt	\N	2026-05-08	Validation suite test voucher	\N	777.00	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	1b75c626-594e-4a15-87a3-0e6ad41958d1	\N	\N	2026-05-13 13:26:23.392231+00	2026-05-13 13:26:23.392231+00	f	\N	\N	\N	\N	\N
923a0f8b-974b-4f68-bf6f-f7ca16565910	b98ec1dc-23db-40d8-b9d6-e8e144da5f43	Receipt	\N	2026-05-08	replay-3d9ffe	\N	777.00	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	956f8b68-30a0-42ee-9f8f-d4e52037c458	\N	\N	2026-05-13 13:26:26.029313+00	2026-05-13 13:26:26.029313+00	f	\N	\N	\N	\N	\N
8b115e16-8f63-44cd-885a-0a1ca568b4a5	04a61e7b-797f-454b-911d-4c1948f93780	Receipt	\N	2026-05-08	Validation suite test voucher	\N	100.00	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	fb92feda-96f4-412e-a999-a68fcd0bde1c	\N	\N	2026-05-13 13:26:28.687018+00	2026-05-13 13:26:28.687018+00	f	\N	\N	\N	\N	\N
171d2b75-0b40-4c2e-9b65-431d8718a23d	09b780ee-35f4-4d12-a8fc-ca07efd5bc00	Receipt	\N	2026-05-08	Validation suite test voucher	\N	777.00	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	27d19365-6c20-4347-9f87-724c94616e89	\N	\N	2026-05-13 13:26:33.948285+00	2026-05-13 13:26:33.948285+00	f	\N	\N	\N	\N	\N
5a4a30ee-4b65-49c6-8d82-426012f026ed	74c493c5-e0ab-4802-9b5a-b64749462c43	Receipt	\N	2026-05-08	count-probe-1356ed422c514d3f8419b910b8ba069f	\N	777.00	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	57daf9e8-9922-4cc0-9710-fa70b1fd51dd	\N	\N	2026-05-13 13:26:36.615238+00	2026-05-13 13:26:36.615238+00	f	\N	\N	\N	\N	\N
7a7f4f81-8f96-4a2e-945e-e5146b567329	58f01ad7-88ad-4eb5-bf49-664a665185f7	Payment	\N	2026-05-16	Test voucher posted while Tally stopped	\N	1000.00	posted	manual	\N	f	\N	f	\N	0.00	0.00	0.00	0.00	f	0.00	\N	\N	\N	0	\N	79910b81-2a28-4fa4-8745-ee9d468a65bc	\N	\N	2026-05-16 11:07:26.213469+00	2026-05-16 11:07:26.213469+00	f	\N	\N	\N	\N	\N
\.


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: account_deletion_requests pk_account_deletion_requests; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_deletion_requests
    ADD CONSTRAINT pk_account_deletion_requests PRIMARY KEY (id);


--
-- Name: audit_logs pk_audit_logs; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT pk_audit_logs PRIMARY KEY (id);


--
-- Name: companies pk_companies; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.companies
    ADD CONSTRAINT pk_companies PRIMARY KEY (id);


--
-- Name: connector_enrollment_codes pk_connector_enrollment_codes; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connector_enrollment_codes
    ADD CONSTRAINT pk_connector_enrollment_codes PRIMARY KEY (id);


--
-- Name: device_tokens pk_device_tokens; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_tokens
    ADD CONSTRAINT pk_device_tokens PRIMARY KEY (id);


--
-- Name: idempotency_keys pk_idempotency_keys; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_keys
    ADD CONSTRAINT pk_idempotency_keys PRIMARY KEY (id);


--
-- Name: ledger_entries pk_ledger_entries; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_entries
    ADD CONSTRAINT pk_ledger_entries PRIMARY KEY (id);


--
-- Name: ledgers pk_ledgers; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledgers
    ADD CONSTRAINT pk_ledgers PRIMARY KEY (id);


--
-- Name: user_companies pk_user_companies; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_companies
    ADD CONSTRAINT pk_user_companies PRIMARY KEY (id);


--
-- Name: users pk_users; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT pk_users PRIMARY KEY (id);


--
-- Name: vouchers pk_vouchers; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT pk_vouchers PRIMARY KEY (id);


--
-- Name: companies uq_companies_gstin; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.companies
    ADD CONSTRAINT uq_companies_gstin UNIQUE (gstin);


--
-- Name: connector_enrollment_codes uq_connector_enrollment_codes_hash; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connector_enrollment_codes
    ADD CONSTRAINT uq_connector_enrollment_codes_hash UNIQUE (code_hash);


--
-- Name: device_tokens uq_device_tokens_token; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_tokens
    ADD CONSTRAINT uq_device_tokens_token UNIQUE (token);


--
-- Name: idempotency_keys uq_idempotency_keys_company_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_keys
    ADD CONSTRAINT uq_idempotency_keys_company_key UNIQUE (company_id, key);


--
-- Name: ledger_entries uq_ledger_entries_voucher_line; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_entries
    ADD CONSTRAINT uq_ledger_entries_voucher_line UNIQUE (voucher_id, line_number);


--
-- Name: ledgers uq_ledgers_company_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledgers
    ADD CONSTRAINT uq_ledgers_company_name UNIQUE (company_id, name);


--
-- Name: ledgers uq_ledgers_company_tally; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledgers
    ADD CONSTRAINT uq_ledgers_company_tally UNIQUE (company_id, tally_master_id);


--
-- Name: user_companies uq_user_companies_user_company; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_companies
    ADD CONSTRAINT uq_user_companies_user_company UNIQUE (user_id, company_id);


--
-- Name: users uq_users_email; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT uq_users_email UNIQUE (email);


--
-- Name: vouchers uq_vouchers_company_number_type; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT uq_vouchers_company_number_type UNIQUE (company_id, voucher_type, voucher_number) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: idx_account_deletion_grace_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_account_deletion_grace_pending ON public.account_deletion_requests USING btree (grace_ends_at) WHERE (status = 'grace_period'::public.account_deletion_status);


--
-- Name: idx_account_deletion_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_account_deletion_user ON public.account_deletion_requests USING btree (user_id);


--
-- Name: idx_audit_logs_company_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_company_created ON public.audit_logs USING btree (company_id, created_at DESC) WHERE (company_id IS NOT NULL);


--
-- Name: idx_audit_logs_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_entity ON public.audit_logs USING btree (entity_type, entity_id);


--
-- Name: idx_audit_logs_request; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_request ON public.audit_logs USING btree (request_id) WHERE (request_id IS NOT NULL);


--
-- Name: idx_audit_logs_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_user ON public.audit_logs USING btree (user_id, created_at DESC) WHERE (user_id IS NOT NULL);


--
-- Name: idx_companies_gstin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_companies_gstin ON public.companies USING btree (gstin) WHERE (gstin IS NOT NULL);


--
-- Name: idx_companies_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_companies_status ON public.companies USING btree (status) WHERE (status = 'active'::public.company_status);


--
-- Name: idx_connector_enrollment_codes_company; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_connector_enrollment_codes_company ON public.connector_enrollment_codes USING btree (company_id, created_at DESC);


--
-- Name: idx_connector_enrollment_codes_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_connector_enrollment_codes_pending ON public.connector_enrollment_codes USING btree (expires_at) WHERE (consumed_at IS NULL);


--
-- Name: idx_device_tokens_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_device_tokens_user_active ON public.device_tokens USING btree (user_id, is_active);


--
-- Name: idx_idempotency_keys_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_idempotency_keys_expires ON public.idempotency_keys USING btree (expires_at);


--
-- Name: idx_ledger_entries_company_ledger; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_entries_company_ledger ON public.ledger_entries USING btree (company_id, ledger_id);


--
-- Name: idx_ledger_entries_ledger; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_entries_ledger ON public.ledger_entries USING btree (ledger_id);


--
-- Name: idx_ledger_entries_voucher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledger_entries_voucher ON public.ledger_entries USING btree (voucher_id);


--
-- Name: idx_ledgers_company; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledgers_company ON public.ledgers USING btree (company_id);


--
-- Name: idx_ledgers_company_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledgers_company_active ON public.ledgers USING btree (company_id, is_active);


--
-- Name: idx_ledgers_company_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledgers_company_group ON public.ledgers USING btree (company_id, group_name);


--
-- Name: idx_ledgers_gstin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledgers_gstin ON public.ledgers USING btree (company_id, gstin) WHERE (gstin IS NOT NULL);


--
-- Name: idx_ledgers_name_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledgers_name_trgm ON public.ledgers USING gin (name_normalized public.gin_trgm_ops);


--
-- Name: idx_ledgers_pan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ledgers_pan ON public.ledgers USING btree (company_id, pan) WHERE (pan IS NOT NULL);


--
-- Name: idx_user_companies_company; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_companies_company ON public.user_companies USING btree (company_id);


--
-- Name: idx_user_companies_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_companies_user ON public.user_companies USING btree (user_id);


--
-- Name: idx_users_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_active ON public.users USING btree (is_active) WHERE (is_active = true);


--
-- Name: idx_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_email ON public.users USING btree (email);


--
-- Name: idx_vouchers_company_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vouchers_company_date ON public.vouchers USING btree (company_id, date DESC);


--
-- Name: idx_vouchers_company_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vouchers_company_status ON public.vouchers USING btree (company_id, status);


--
-- Name: idx_vouchers_company_type_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vouchers_company_type_date ON public.vouchers USING btree (company_id, voucher_type, date DESC);


--
-- Name: idx_vouchers_optional_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vouchers_optional_pending ON public.vouchers USING btree (company_id, date DESC) WHERE ((is_optional_in_tally = true) AND (approved_to_regular_at IS NULL) AND (status <> ALL (ARRAY['cancelled'::public.voucher_status, 'rejected_optional'::public.voucher_status])));


--
-- Name: idx_vouchers_source_ingestion; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vouchers_source_ingestion ON public.vouchers USING btree (source_ingestion_id) WHERE (source_ingestion_id IS NOT NULL);


--
-- Name: idx_vouchers_unposted_to_tally; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vouchers_unposted_to_tally ON public.vouchers USING btree (company_id) WHERE ((status = 'posted'::public.voucher_status) AND (tally_posted_at IS NULL));


--
-- Name: audit_logs audit_logs_no_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER audit_logs_no_delete BEFORE DELETE ON public.audit_logs FOR EACH ROW EXECUTE FUNCTION public.prevent_audit_modification();


--
-- Name: audit_logs audit_logs_no_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER audit_logs_no_update BEFORE UPDATE ON public.audit_logs FOR EACH ROW EXECUTE FUNCTION public.prevent_audit_modification();


--
-- Name: account_deletion_requests trg_account_deletion_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_account_deletion_updated_at BEFORE UPDATE ON public.account_deletion_requests FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: companies trg_companies_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_companies_updated_at BEFORE UPDATE ON public.companies FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: device_tokens trg_device_tokens_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_device_tokens_updated_at BEFORE UPDATE ON public.device_tokens FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: ledgers trg_ledgers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ledgers_updated_at BEFORE UPDATE ON public.ledgers FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: user_companies trg_user_companies_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_user_companies_updated_at BEFORE UPDATE ON public.user_companies FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: users trg_users_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: vouchers trg_vouchers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vouchers_updated_at BEFORE UPDATE ON public.vouchers FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: account_deletion_requests fk_account_deletion_requests_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_deletion_requests
    ADD CONSTRAINT fk_account_deletion_requests_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: audit_logs fk_audit_logs_company_id_companies; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT fk_audit_logs_company_id_companies FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE RESTRICT;


--
-- Name: audit_logs fk_audit_logs_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT fk_audit_logs_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: companies fk_companies_created_by_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.companies
    ADD CONSTRAINT fk_companies_created_by_users FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: connector_enrollment_codes fk_connector_enrollment_codes_company_id_companies; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connector_enrollment_codes
    ADD CONSTRAINT fk_connector_enrollment_codes_company_id_companies FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: connector_enrollment_codes fk_connector_enrollment_codes_created_by_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connector_enrollment_codes
    ADD CONSTRAINT fk_connector_enrollment_codes_created_by_users FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: device_tokens fk_device_tokens_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_tokens
    ADD CONSTRAINT fk_device_tokens_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: idempotency_keys fk_idempotency_keys_company_id_companies; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_keys
    ADD CONSTRAINT fk_idempotency_keys_company_id_companies FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: idempotency_keys fk_idempotency_keys_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_keys
    ADD CONSTRAINT fk_idempotency_keys_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: ledger_entries fk_ledger_entries_company_id_companies; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_entries
    ADD CONSTRAINT fk_ledger_entries_company_id_companies FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE RESTRICT;


--
-- Name: ledger_entries fk_ledger_entries_ledger_id_ledgers; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_entries
    ADD CONSTRAINT fk_ledger_entries_ledger_id_ledgers FOREIGN KEY (ledger_id) REFERENCES public.ledgers(id) ON DELETE RESTRICT;


--
-- Name: ledger_entries fk_ledger_entries_voucher_id_vouchers; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledger_entries
    ADD CONSTRAINT fk_ledger_entries_voucher_id_vouchers FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id) ON DELETE CASCADE;


--
-- Name: ledgers fk_ledgers_company_id_companies; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledgers
    ADD CONSTRAINT fk_ledgers_company_id_companies FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE RESTRICT;


--
-- Name: ledgers fk_ledgers_parent_ledger_id_ledgers; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ledgers
    ADD CONSTRAINT fk_ledgers_parent_ledger_id_ledgers FOREIGN KEY (parent_ledger_id) REFERENCES public.ledgers(id) ON DELETE SET NULL;


--
-- Name: user_companies fk_user_companies_company_id_companies; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_companies
    ADD CONSTRAINT fk_user_companies_company_id_companies FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE RESTRICT;


--
-- Name: user_companies fk_user_companies_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_companies
    ADD CONSTRAINT fk_user_companies_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: vouchers fk_vouchers_approved_by_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT fk_vouchers_approved_by_users FOREIGN KEY (approved_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: vouchers fk_vouchers_approved_to_regular_by_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT fk_vouchers_approved_to_regular_by_users FOREIGN KEY (approved_to_regular_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: vouchers fk_vouchers_company_id_companies; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT fk_vouchers_company_id_companies FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE RESTRICT;


--
-- Name: vouchers fk_vouchers_created_by_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT fk_vouchers_created_by_users FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: vouchers fk_vouchers_optional_rejected_by_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT fk_vouchers_optional_rejected_by_users FOREIGN KEY (optional_rejected_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict nzufTnBeDBXruV4bJyXPQKy5jJWiqtENX4x9aQapIoiplPYUdRTuyV9QUG4kDh2

