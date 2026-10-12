"""LLM call: images -> ExtractedInvoice.

Per docs/EXTRACTION_CONTRACT.md "LLM choice" / "Retry policy". The
contract's primary (Claude)/fallback (GPT-4o) pair is the paid-tier
upgrade path; the pilot runs on Gemini's free tier (see CLAUDE.md
"Phase 1 scope") via the same swappable-model shape
(`INVOICE_EXTRACTION_MODEL`).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.config import get_settings
from app.services.extraction.extraction_schema import (
    SCHEMA_VERSION,
    ExtractedInvoice,
)

logger = logging.getLogger("app.services.extraction.invoice_extractor")

_PROMPT_VERSION = "1.0"
_PROMPT_PATH = Path(__file__).parent / "prompts" / "invoice_v1.txt"

_SIMPLIFIED_SUFFIX = (
    "\n\nIMPORTANT: your previous response was not valid JSON matching "
    "the schema. Return ONLY a single valid JSON object. No markdown "
    "code fences, no prose before or after, no trailing commas."
)


class ExtractionHardFailure(Exception):
    """The LLM call itself failed (network/5xx/timeout). Retryable by
    the caller's backoff policy."""


class ExtractionMalformedOutput(Exception):
    """The LLM never returned valid JSON matching the schema, even
    after one simplified-prompt retry. Not retryable automatically."""


@lru_cache(maxsize=1)
def _prompt_text() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _client() -> genai.Client:
    settings = get_settings()
    if settings.GEMINI_API_KEY is None:
        raise ExtractionHardFailure(
            "GEMINI_API_KEY is not configured; cannot call the "
            "extraction model."
        )
    return genai.Client(api_key=settings.GEMINI_API_KEY.get_secret_value())


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -len("```")]
        if text.startswith("json"):
            text = text[len("json") :]
    return text.strip()


def _call_model(image_jpegs: list[bytes], prompt: str) -> str:
    client = _client()
    model = get_settings().INVOICE_EXTRACTION_MODEL
    parts: list[types.Part | str] = [prompt]
    for jpeg in image_jpegs:
        parts.append(types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"))
    try:
        response = client.models.generate_content(
            model=model,
            contents=parts,  # type: ignore[arg-type]  # SDK wants Sequence; list is fine at runtime
        )
    except Exception as exc:  # network / API error -- transport failure
        raise ExtractionHardFailure(str(exc)) from exc
    text = response.text
    if not text:
        raise ExtractionMalformedOutput("empty response from model")
    return text


def extract_invoice(
    image_jpegs: list[bytes],
) -> tuple[ExtractedInvoice, dict[str, str]]:
    """Call the vision model and parse its reply.

    Returns (extracted, meta) where meta matches the
    `parsed_data._meta` shape from EXTRACTION_CONTRACT.md "Versioning".
    Raises `ExtractionHardFailure` (transport) or
    `ExtractionMalformedOutput` (bad JSON/schema, after one internal
    retry with a simplified prompt).
    """
    base_prompt = _prompt_text()
    raw_text = _call_model(image_jpegs, base_prompt)

    extracted = _try_parse(raw_text)
    if extracted is None:
        logger.warning(
            "extraction returned malformed output, retrying with "
            "simplified prompt"
        )
        raw_text = _call_model(image_jpegs, base_prompt + _SIMPLIFIED_SUFFIX)
        extracted = _try_parse(raw_text)
    if extracted is None:
        raise ExtractionMalformedOutput(
            "LLM did not return valid JSON matching the schema, even "
            "after a simplified-prompt retry"
        )

    meta = {
        "prompt_version": _PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "model": get_settings().INVOICE_EXTRACTION_MODEL,
    }
    return extracted, meta


def _try_parse(raw_text: str) -> ExtractedInvoice | None:
    try:
        payload = json.loads(_strip_fences(raw_text))
    except json.JSONDecodeError:
        logger.warning("extraction output was not valid JSON: %.200s", raw_text)
        return None
    try:
        return ExtractedInvoice.model_validate(payload)
    except ValidationError as exc:
        logger.warning("extraction output failed schema validation: %s", exc)
        return None
