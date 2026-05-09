# Invoice Extraction Contract

**Status:** Frozen for Phase 1 (the wedge feature). Other extraction types (bank statements, SMS, GSTR-2B) get their own contracts in their respective phases.

This document specifies how an uploaded invoice (photo or PDF) becomes a structured `DraftVoucher`. It defines the LLM prompt, the JSON schema the LLM must return, the validation rules, and the retry/fallback policy.

The invoice scan is the wedge feature for TaxMind Books. Its accuracy and reliability determine whether the first 10 customers stay. This contract is therefore stricter than most others.

## End-to-end flow (v1.2 — Flow B)

```
1. User uploads photo/PDF via mobile app
       │
       ▼
2. POST /api/v1/ingestions/  ──►  Ingestion row created (status=received)
                                   File stored in S3 at s3_object_key
       │
       ▼
3. Celery task `extract_invoice` enqueued
       │
       ▼
4. Worker downloads file from S3
       │
       ▼
5. Worker calls Claude Vision with prompt + image (or PDF page images)
       │
       ▼
6. LLM returns structured JSON
       │
       ▼
7. Validator runs:
   - JSON schema check
   - GST math check
   - GSTIN format check
   - Date plausibility check
       │
       ▼
8. Party matcher runs:
   - GSTIN exact match → ledger
   - PAN exact match → ledger
   - Fuzzy name match → top 3 candidates
       │
       ▼
9. Voucher created with is_optional_in_tally=true,
   status='optional', confidence_score, flags
   The ingestion's posted_voucher_id is set to point to it.
       │
       ▼
10. Posting task enqueues to connector with as_optional=true.
    Tally Optional voucher created (no financial impact).
       │
       ▼
11. Admin reviews via mobile app or in Tally directly.
    Admin approves → connector.approve_optional_voucher → Tally flips to Regular
                  → audit log: voucher.approved_to_regular
                  → voucher.is_optional_in_tally = false
                  → voucher.approved_to_regular_at set
                  → voucher.status = 'posted'
    Admin rejects → connector.reject_optional_voucher → Tally deletes voucher
                  → audit log: voucher.rejected_optional
                  → voucher.status = 'rejected_optional'
                  → voucher.optional_rejection_reason set
```

## Inputs

A successfully created `Ingestion` with:
- `source` ∈ {`photo`, `pdf`}
- `s3_object_key` pointing to an uploaded file
- `content_type` ∈ {`image/jpeg`, `image/png`, `image/heic`, `application/pdf`}
- `content_size` ≤ 10 MB

If the file fails initial validation (corrupt, password-protected PDF, content-type mismatch), the worker sets the ingestion to `status=failed` with `failure_reason` and does not call the LLM.

## Image preparation

Before sending to the LLM:

1. **HEIC → JPEG.** iPhone uploads in HEIC format; convert with `pyheif` to JPEG quality 95.
2. **PDF → page images.** Each PDF page rendered to PNG at 200 DPI using `pdf2image` (Poppler).
3. **Multi-page handling.** If PDF has more than 5 pages, only the first 5 are processed; a flag is added to the draft (`flags: ["multi_page_truncated"]`) and the user is notified. Real invoices rarely exceed 5 pages; if they do, the user can split.
4. **Image downscaling.** Long edge resized to max 2048 px (preserves aspect ratio). Saves cost and bandwidth without losing OCR accuracy on typed bills.
5. **EXIF rotation applied.** Phone-uploaded JPEGs have orientation metadata that some libraries respect and others ignore; we apply rotation explicitly to ensure the LLM sees the image right-side-up.

The prepared images are stored in S3 alongside the original (key suffix `_processed_p1.jpg`, etc.) for debugging and re-processing without re-rendering.

## LLM choice

**Primary:** Claude Sonnet 4 (or current generation) via Anthropic Vision API.

**Fallback:** GPT-4o or GPT-4-Turbo via OpenAI Vision (used only if primary fails 3 consecutive times within a 5-minute window, indicating an Anthropic outage).

**Model selection rationale:**
- Vision-capable model required (must accept images).
- Strong structured output capability (JSON mode or tool use).
- Reasonable cost per image. Current Anthropic pricing makes this feasible for ₹999/month/MSME with average 50 invoices/month per company.

The model identifier is configurable via `INVOICE_EXTRACTION_MODEL` env var. The default is set in `backend/app/services/extraction/invoice_extractor.py`.

## The prompt

The system prompt is fixed text, version-controlled in `backend/app/services/extraction/prompts/invoice_v1.txt`. It is loaded at process start and cached. Updating the prompt requires bumping `_PROMPT_VERSION` in the extractor module; the version is recorded in the draft's `parsed_data._meta.prompt_version`.

```
You are an expert at extracting structured data from Indian GST tax invoices and cash memos.

The user will provide one or more images of an invoice. The images may be:
- Typed GST tax invoices (most common)
- Cash memos (no GSTIN; smaller establishments)
- Handwritten kirana / wholesale bills (rare but supported)
- Multi-page invoices (each page provided separately, in order)

Your job is to return a JSON object matching the schema below. Return ONLY the JSON, no prose, no markdown fences.

Schema:
{
  "vendor_name": string | null,
  "vendor_gstin": string | null,                 // 15-char Indian GSTIN, exact format
  "vendor_pan": string | null,                   // derivable from GSTIN if present (chars 3-12)
  "vendor_address": string | null,
  "vendor_state_code": string | null,            // 2-digit state code, derivable from GSTIN
  "buyer_name": string | null,                   // the recipient (the user's company)
  "buyer_gstin": string | null,
  "invoice_number": string | null,
  "invoice_date": string | null,                 // ISO 8601 YYYY-MM-DD
  "place_of_supply": string | null,              // 2-digit state code
  "line_items": [
    {
      "description": string,
      "hsn_sac": string | null,                  // HSN/SAC code if present
      "quantity": string | null,                 // string to preserve fractional units
      "unit": string | null,                     // "Nos", "Kg", "Ltr", etc.
      "rate": string | null,                     // per-unit price
      "amount": string,                          // line total before GST
      "gst_rate": string | null                  // percentage as string, e.g. "18"
    }
  ],
  "subtotal": string | null,                     // sum of line item amounts
  "discount": string | null,
  "cgst": string | null,
  "sgst": string | null,
  "igst": string | null,
  "cess": string | null,
  "round_off": string | null,
  "total_amount": string,                        // grand total
  "amount_in_words": string | null,
  "payment_terms": string | null,
  "notes": string | null,
  "extraction_confidence": number,               // your overall confidence 0.0-1.0
  "extraction_warnings": [string],               // any concerns about the extraction
  "is_likely_invoice": boolean                   // false if image is not actually an invoice
}

Rules for amounts:
- ALL monetary values are strings with exactly 2 decimal places. Examples: "1500.00", "75.50".
- Do not include the rupee symbol or currency code.
- Do not include thousand separators (no "1,500.00"; use "1500.00").
- If a value is not visible in the image, use null. Do not guess.

Rules for dates:
- Format YYYY-MM-DD.
- Indian invoices commonly use DD/MM/YYYY or DD-MM-YYYY. Convert correctly: "07/05/2026" is 7 May 2026, not 5 July 2026.
- If only month and year are visible, use the 1st of the month.

Rules for GSTIN:
- Format: 2 digits + 5 letters + 4 digits + 1 letter + 1 alphanumeric + Z + 1 alphanumeric
- Total 15 characters
- If you cannot read the full GSTIN clearly, return null. Do not partially fill.

Rules for confidence:
- 0.95+: typed invoice, all fields clear, GST math reconciles
- 0.80-0.95: minor uncertainty (one or two fields unclear, or handwritten amounts)
- 0.60-0.80: significant uncertainty (multiple fields unclear, blurry image, partial occlusion)
- Below 0.60: do not return; instead set is_likely_invoice=false with a warning

Rules for warnings:
- Add a warning string for each notable concern. Examples:
  - "vendor_gstin partially obscured"
  - "amount mismatch: line items sum to 11700 but stated subtotal is 11800"
  - "invoice_date appears to be in the future"
  - "image is blurry; some text may be misread"
  - "no GSTIN found; this appears to be a cash memo"

If the image is clearly not an invoice (a screenshot of WhatsApp, a selfie, a receipt for personal expense, etc.), set is_likely_invoice=false, total_amount="0.00", and add a warning explaining what the image shows.
```

The user message contains the prepared images as content blocks.

## Expected JSON schema (Pydantic)

The validator uses this Pydantic model. Drift between the prompt and this schema is caught by a unit test.

```python
# backend/app/services/extraction/extraction_schema.py
from decimal import Decimal
from datetime import date
from typing import Annotated
from pydantic import BaseModel, Field, BeforeValidator, ConfigDict


def _parse_money(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    return Decimal(v)


ExtractionMoney = Annotated[Decimal | None, BeforeValidator(_parse_money)]


class InvoiceLineItem(BaseModel):
    description: str
    hsn_sac: str | None = None
    quantity: str | None = None
    unit: str | None = None
    rate: ExtractionMoney = None
    amount: ExtractionMoney
    gst_rate: str | None = None


class ExtractedInvoice(BaseModel):
    """The contract: what the LLM must return."""
    model_config = ConfigDict(extra="forbid")    # reject unknown fields

    vendor_name: str | None = None
    vendor_gstin: str | None = None
    vendor_pan: str | None = None
    vendor_address: str | None = None
    vendor_state_code: str | None = None
    buyer_name: str | None = None
    buyer_gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    place_of_supply: str | None = None
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    subtotal: ExtractionMoney = None
    discount: ExtractionMoney = None
    cgst: ExtractionMoney = None
    sgst: ExtractionMoney = None
    igst: ExtractionMoney = None
    cess: ExtractionMoney = None
    round_off: ExtractionMoney = None
    total_amount: ExtractionMoney
    amount_in_words: str | None = None
    payment_terms: str | None = None
    notes: str | None = None
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    extraction_warnings: list[str] = Field(default_factory=list)
    is_likely_invoice: bool
```

If parsing fails (LLM returned malformed JSON, extra fields, missing required fields), the validator records the failure and falls into the retry policy below.

## Validation rules

After successful schema parse, the validator runs business rules:

### Rule V1: GSTIN format

If `vendor_gstin` or `buyer_gstin` is non-null, it must match `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$`. Failure → set field to null, append warning.

### Rule V2: PAN derivation

If `vendor_gstin` is set and `vendor_pan` is null, derive PAN as `vendor_gstin[2:12]`. Same for buyer.

### Rule V3: State code derivation

If `vendor_gstin` is set, `vendor_state_code` should equal `vendor_gstin[0:2]`. If they conflict, prefer the GSTIN-derived value and warn.

### Rule V4: GST math

If `gst_applicable` (any of cgst/sgst/igst > 0):

```
expected_total = subtotal - (discount or 0) + (cgst or 0) + (sgst or 0) + (igst or 0) + (cess or 0) + (round_off or 0)
abs(expected_total - total_amount) <= Decimal("1.00")
```

`Decimal("1.00")` tolerance accounts for invoices that round their own subtotal display. Failure → flag `gst_math_mismatch` is added; draft `review_status` becomes `needs_review`.

### Rule V5: CGST = SGST

For intra-state supplies, CGST and SGST should be equal. If they differ by more than `Decimal("1.00")`, flag `gst_split_mismatch`.

### Rule V6: Date plausibility

`invoice_date` must satisfy:
- Not in the future (with 1-day grace for time zones)
- Not more than 2 years in the past (configurable; covers fiscal-year overruns)

Failure → flag `date_implausible`, set `review_status=needs_review`.

### Rule V7: Self-billing detection

If `vendor_gstin == buyer_gstin == active_company.gstin`, this is the user's own outgoing invoice, not an incoming one. Flag `self_invoice`, set `suggested_voucher_type=Sales` instead of `Purchase`.

### Rule V8: Total presence

`total_amount` must be present and > 0. If the LLM returned `total_amount=0.00` with `is_likely_invoice=false`, the worker marks the ingestion `status=discarded` (not failed) and creates no draft. The user gets a "we couldn't read this as an invoice" notification.

### Rule V9: Confidence flooring

After all rules, the validator computes the **post-validation confidence**:

```
confidence = llm_confidence * (1 - 0.10 * len(critical_warnings)) - 0.05 * len(non_critical_warnings)
```

Where critical warnings include `gst_math_mismatch`, `date_implausible`, `vendor_gstin_invalid_format`. Non-critical: anything else.

The clamped result is stored as `draft_vouchers.confidence_score`.

## Auto-post threshold (v1.2 — Flow B)

```
if confidence >= AUTO_POST_CONFIDENCE_THRESHOLD (default 0.90)
   AND no critical flags
   AND vendor_ledger matched (not "new_party")
   AND tenant has auto_post enabled
   → status = "optional", is_optional_in_tally = true
   → Voucher posted to Tally as Optional via the connector
   → No financial impact until admin approves
otherwise
   → status = "optional", is_optional_in_tally = true
   → Voucher posted to Tally as Optional via the connector
   → Surfaces in mobile review queue with low-confidence flag
```

**v1.2 change:** the amount cap from v1.1 is **removed**. Confidence is the only auto-post criterion. Every AI-extracted voucher — high-confidence or low — lands in Tally as an Optional voucher. Optional vouchers do not affect financial statements (P&L, balance sheet, trial balance) until an admin approves them via `POST /api/v1/vouchers/{id}/approve-to-regular`, which flips them to Regular in Tally.

The safety property previously provided by the amount cap is now provided by the Optional voucher mechanism: no AI-extracted entry can affect a customer's books without explicit human approval, regardless of amount or confidence.

**The mobile app shows two unified queues:**
1. **Pending Approval** — confidence ≥ 0.90, awaiting admin approval. These are the "high quality" extractions; admin reviews briefly and approves.
2. **Needs Review** — confidence < 0.90 OR critical flags. Admin scrutinizes, may edit fields before approving.

Both feed into the same Tally Optional voucher state. The classification is informational (sorts the queue by attention required), not behavioral.

## Party matching

Once the invoice is extracted and validated, the party matcher runs against the company's `ledgers` table:

### Tier 1: GSTIN exact match
If `vendor_gstin` matches a ledger's `gstin` for the active company → that ledger is the suggested party. confidence boost +0.05.

### Tier 2: PAN exact match
If `vendor_pan` (or derived from GSTIN) matches a ledger's `pan` → that ledger is suggested. Tier-2 flag `matched_via_pan_not_gstin`.

### Tier 3: Fuzzy name match
Using PostgreSQL `pg_trgm` similarity on `name_normalized`, find the top 3 candidates with similarity ≥ 0.6. The top candidate becomes the suggestion. Flag `fuzzy_party_match` is added.

`name_normalized` is `vendor_name` lowercased, with stop-words stripped (Pvt, Ltd, Private, Limited, And, &, Co, Company, Enterprises, Trading, Traders, etc.) and punctuation removed.

### Tier 4: No match
No suggestion. Flag `new_party`. The mobile app prompts the user to either "Create new ledger" or "Pick existing ledger" before posting.

## Retry policy

LLM extraction failures fall into three buckets:

### Hard failure
The LLM call itself fails (HTTP 5xx from Anthropic, timeout, network error). Celery retries with exponential backoff: 30s, 1m, 2m, 5m, 15m. Max 5 attempts.

### Soft failure: malformed output
The LLM returns text that isn't valid JSON or doesn't match the schema. The worker:
1. Logs the raw LLM output for debugging.
2. Retries once with a simplified prompt that asks the LLM to return strict JSON only.
3. If still fails, marks ingestion `status=failed` with `failure_reason="extraction_malformed_output"`.

### Soft failure: low confidence
The LLM returns valid JSON with `extraction_confidence < 0.40`. This usually means the image is unreadable. The worker:
1. Does not retry.
2. Creates a draft with `review_status=needs_review`, `flags=["low_extraction_confidence"]`.
3. The user sees the parsed fields (whatever they are) and can correct manually.

## Cost / latency budget

Per-image budget:
- LLM call: ≤ 8 seconds median, 20 seconds p99.
- Total worker wall time (download + prep + LLM + validate + party-match + DB write): ≤ 12 seconds median.

The mobile app shows a "processing" state during this window; the user does not block.

Anthropic Sonnet 4 pricing (rough estimate, subject to provider changes): per invoice extraction averages ~$0.01–$0.02 depending on image size. At 50 invoices/month/MSME, that's $0.50–$1.00/month/MSME just for extraction LLM, well within ₹999/month pricing.

## Versioning

The prompt and schema are versioned. Every draft records the `prompt_version` and `schema_version` it was produced with:

```python
draft.parsed_data["_meta"] = {
    "prompt_version": "1.0",
    "schema_version": "1.0",
    "model": "claude-sonnet-4-20250514",
    "extracted_at": "2026-05-08T10:00:00Z",
    "raw_llm_response_s3_key": "extractions/<draft_id>/raw.json",   # for audit
}
```

The raw LLM response is stored in S3 (not in the DB) for one year. This is the only way to debug "why did extraction get this wrong" after the fact, and to evaluate prompt changes against historical examples.

When the prompt version changes, existing drafts are not re-extracted automatically. A separate admin tool can re-run extraction on drafts older than version X to compare.

## Dataset for regression

A test corpus lives in `tests/fixtures/invoices/`. It contains:

- 30 typed GST invoices (real PDFs and photos, anonymized — vendor and buyer names changed to fictional)
- 15 cash memos (no GSTIN)
- 5 handwritten/wholesale bills

Each fixture has a `_expected.json` file with the human-validated correct extraction. The integration test `tests/integration/test_invoice_extraction_corpus.py` runs the worker against each fixture and compares.

The pass criterion (per architecture doc Section 5.5):
- ≥80% of typed GST invoices extracted at confidence ≥ 0.90 with no critical flags
- ≥95% GSTIN extraction accuracy when legible
- Total amount within ±₹1 of stated total

The test runs as part of CI on a nightly schedule (not on every PR — it costs LLM tokens). PR-time runs use a much smaller smoke set of 5 invoices.

## Security and privacy

- Uploaded files are encrypted at rest in S3 (server-side encryption with S3-managed keys).
- The S3 bucket is private; access via signed URLs only.
- Files are retained 90 days, then archived to Glacier per the architecture doc retention policy.
- The LLM provider receives the image only. No customer name, no GSTIN of the active company, no other identifying context. The prompt is generic.
- Anthropic API contracts state that messages are not used for training. The configured account uses the API tier, not the consumer Claude.ai tier.
- LLM raw responses (which contain the extracted data) are also encrypted at rest and access-controlled.

## Forbidden patterns

- **Calling the LLM with the buyer's identity in the prompt.** Each call's prompt is the same generic system prompt. We don't pass company name to the LLM.
- **Trusting the LLM's confidence blindly.** Post-validation confidence is computed by the validator and is what drives auto-post. The LLM's self-reported confidence is one input among several.
- **Auto-posting without GST math reconciliation.** Drafts that fail Rule V4 are always `needs_review`, regardless of LLM confidence.
- **Storing LLM raw responses in the DB.** They go to S3. The DB stores the parsed structured form, not the raw text.
- **Re-extracting on read.** Once a draft exists, reading it does not re-call the LLM. Re-extraction is an explicit admin action.
- **Sending non-image data to a vision model.** The worker validates content_type before the LLM call.

## Test cases the human runs during validation

Validation report includes:

1. Upload a clean typed GST invoice via mobile app. Verify within 12 seconds:
   - Ingestion appears with status=extracted.
   - Draft voucher exists with confidence ≥ 0.90.
   - vendor_gstin extracted exactly.
   - total_amount within ₹1 of the actual invoice total.
   - GST split (CGST/SGST) reconciles.
2. Upload a cash memo (no GSTIN). Verify:
   - Draft created with `flags=["new_party"]` (assuming vendor not already a ledger).
   - vendor_gstin is null.
   - review_status = needs_review.
3. Upload a blurry photo. Verify:
   - confidence < 0.80.
   - flag `low_extraction_confidence` or similar.
   - review_status = needs_review.
4. Upload a non-invoice image (a selfie). Verify:
   - is_likely_invoice = false.
   - Ingestion status = discarded.
   - No draft created.
5. Upload a multi-page PDF (3 pages of one invoice). Verify all pages contributed to extraction (line items from later pages appear).
6. Upload an invoice from a vendor already in the ledger book (matched by GSTIN). Verify:
   - suggested_party_ledger_id is set.
   - flags do not include `new_party`.
7. Upload an invoice from a vendor with a slightly different name spelling than the ledger (e.g., "Sharma Tradrs" vs "Sharma Traders"). Verify fuzzy match suggests the existing ledger with `flags=["fuzzy_party_match"]`.
8. Approve a draft. Verify a real Voucher is created and (if connector online) posted to Tally.
9. Re-upload the same image with the same idempotency key. Verify only one ingestion and one draft exist.
10. Upload an invoice during an Anthropic outage (simulate by setting an invalid `ANTHROPIC_API_KEY`). Verify:
    - Worker fails the call.
    - Celery retries with backoff.
    - After max attempts, ingestion status = failed with reason = "extraction_unavailable".
    - User notified in the UI.

If any of 1, 4, 6, 8, 9 fail, the wedge feature is broken; the phase does not pass.
