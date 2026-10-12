"""LLM call: images -> ExtractedInvoice.

Per docs/EXTRACTION_CONTRACT.md "LLM choice" / "Retry policy". The
contract's primary (Claude)/fallback (GPT-4o) pair is mirrored here as
Gemini (free tier, pilot default) primary / GPT-4o-mini fallback,
switched in automatically after 3 consecutive primary failures within
a 5-minute window -- the contract's own outage-detection rule, just
with cheaper models (see CLAUDE.md "Phase 1 scope").
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import openai
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

# Outage-detection failover, per EXTRACTION_CONTRACT.md "LLM choice":
# "used only if primary fails 3 consecutive times within a 5-minute
# window". Module-level state is fine here -- same process-local
# acceptance as connector_registry; a restart just re-tries primary.
FAILOVER_THRESHOLD = 3
FAILOVER_WINDOW = timedelta(minutes=5)


class ExtractionHardFailure(Exception):
    """The LLM call itself failed (network/5xx/timeout). Retryable by
    the caller's backoff policy."""


class ExtractionMalformedOutput(Exception):
    """The LLM never returned valid JSON matching the schema, even
    after one simplified-prompt retry. Not retryable automatically."""


@dataclass
class _FailoverState:
    consecutive_failures: int = 0
    first_failure_at: datetime | None = None


_failover = _FailoverState()


def _primary_is_down() -> bool:
    if _failover.consecutive_failures < FAILOVER_THRESHOLD:
        return False
    assert _failover.first_failure_at is not None
    if datetime.now(UTC) - _failover.first_failure_at > FAILOVER_WINDOW:
        reset_failover()
        return False
    return True


def _record_primary_failure() -> None:
    now = datetime.now(UTC)
    if (
        _failover.first_failure_at is None
        or now - _failover.first_failure_at > FAILOVER_WINDOW
    ):
        _failover.first_failure_at = now
        _failover.consecutive_failures = 1
    else:
        _failover.consecutive_failures += 1


def reset_failover() -> None:
    """Clear failover state. Exposed for tests -- the state is a
    module-level singleton so tests must reset it between runs."""
    _failover.consecutive_failures = 0
    _failover.first_failure_at = None


@lru_cache(maxsize=1)
def _prompt_text() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _gemini_client() -> genai.Client:
    settings = get_settings()
    if settings.GEMINI_API_KEY is None:
        raise ExtractionHardFailure(
            "GEMINI_API_KEY is not configured; cannot call the "
            "extraction model."
        )
    return genai.Client(api_key=settings.GEMINI_API_KEY.get_secret_value())


def _openai_client() -> openai.OpenAI:
    settings = get_settings()
    if settings.OPENAI_API_KEY is None:
        raise ExtractionHardFailure(
            "OPENAI_API_KEY is not configured; cannot call the "
            "fallback extraction model."
        )
    return openai.OpenAI(api_key=settings.OPENAI_API_KEY.get_secret_value())


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -len("```")]
        if text.startswith("json"):
            text = text[len("json") :]
    return text.strip()


def _call_gemini(image_jpegs: list[bytes], prompt: str, model: str) -> str:
    client = _gemini_client()
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


def _call_openai(image_jpegs: list[bytes], prompt: str, model: str) -> str:
    client = _openai_client()
    content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
    for jpeg in image_jpegs:
        b64 = base64.b64encode(jpeg).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],  # type: ignore[list-item]
        )
    except Exception as exc:  # network / API error -- transport failure
        raise ExtractionHardFailure(str(exc)) from exc
    text = response.choices[0].message.content
    if not text:
        raise ExtractionMalformedOutput("empty response from model")
    return text


def _call_model(image_jpegs: list[bytes], prompt: str) -> tuple[str, str]:
    """Call the primary model, failing over to OpenAI after an outage.

    Returns (raw_text, model_actually_used).
    """
    settings = get_settings()
    if not _primary_is_down():
        try:
            text = _call_gemini(
                image_jpegs, prompt, settings.INVOICE_EXTRACTION_MODEL
            )
        except ExtractionHardFailure:
            _record_primary_failure()
            if not _primary_is_down():
                raise
            logger.warning(
                "primary extraction model failed %d times within %s; "
                "failing over to %s",
                _failover.consecutive_failures,
                FAILOVER_WINDOW,
                settings.OPENAI_EXTRACTION_MODEL,
            )
        else:
            reset_failover()
            return text, settings.INVOICE_EXTRACTION_MODEL

    return (
        _call_openai(image_jpegs, prompt, settings.OPENAI_EXTRACTION_MODEL),
        settings.OPENAI_EXTRACTION_MODEL,
    )


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
    raw_text, model_used = _call_model(image_jpegs, base_prompt)

    extracted = _try_parse(raw_text)
    if extracted is None:
        logger.warning(
            "extraction returned malformed output, retrying with "
            "simplified prompt"
        )
        raw_text, model_used = _call_model(
            image_jpegs, base_prompt + _SIMPLIFIED_SUFFIX
        )
        extracted = _try_parse(raw_text)
    if extracted is None:
        raise ExtractionMalformedOutput(
            "LLM did not return valid JSON matching the schema, even "
            "after a simplified-prompt retry"
        )

    meta = {
        "prompt_version": _PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "model": model_used,
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
