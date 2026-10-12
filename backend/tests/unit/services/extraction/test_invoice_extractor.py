"""Unit tests for invoice_extractor, mocking both LLM clients entirely.

No network call ever happens here -- `_gemini_client()`/`_openai_client()`
are monkeypatched to return fakes with canned responses, per the retry
and failover policy in docs/EXTRACTION_CONTRACT.md.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.services.extraction import invoice_extractor
from app.services.extraction.invoice_extractor import (
    ExtractionHardFailure,
    ExtractionMalformedOutput,
    extract_invoice,
)

VALID_JSON = (
    '{"vendor_name": "Sharma Traders", "total_amount": "100.00", '
    '"extraction_confidence": 0.95, "is_likely_invoice": true}'
)


@pytest.fixture(autouse=True)
def _reset_failover_state() -> None:
    # Module-level failover state is a singleton -- tests must not
    # leak consecutive-failure counts into each other.
    invoice_extractor.reset_failover()


class _FakeModels:
    def __init__(self, replies: list[str | Exception]) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents):  # type: ignore[no-untyped-def]
        self.calls.append({"model": model, "contents": contents})
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply)


class _FakeGeminiClient:
    def __init__(self, replies: list[str | Exception]) -> None:
        self.models = _FakeModels(replies)


class _FakeOpenAIClient:
    def __init__(self, replies: list[str | Exception]) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, *, model, messages):  # type: ignore[no-untyped-def]
        self.calls.append({"model": model, "messages": messages})
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        message = SimpleNamespace(content=reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _patch_client(monkeypatch, replies: list[str | Exception]) -> _FakeGeminiClient:  # type: ignore[no-untyped-def]
    client = _FakeGeminiClient(replies)
    monkeypatch.setattr(invoice_extractor, "_gemini_client", lambda: client)
    return client


def _patch_openai(monkeypatch, replies: list[str | Exception]) -> _FakeOpenAIClient:  # type: ignore[no-untyped-def]
    client = _FakeOpenAIClient(replies)
    monkeypatch.setattr(invoice_extractor, "_openai_client", lambda: client)
    return client


def test_happy_path_parses_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_client(monkeypatch, [VALID_JSON])
    extracted, meta = extract_invoice([b"fake-jpeg-bytes"])
    assert extracted.vendor_name == "Sharma Traders"
    assert meta["schema_version"] == "1.0"
    assert len(client.models.calls) == 1


def test_strips_markdown_fences(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, [f"```json\n{VALID_JSON}\n```"])
    extracted, _ = extract_invoice([b"fake"])
    assert extracted.total_amount == pytest.approx(100.00)


def test_malformed_then_simplified_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _patch_client(monkeypatch, ["not json at all", VALID_JSON])
    extracted, _ = extract_invoice([b"fake"])
    assert extracted.vendor_name == "Sharma Traders"
    assert len(client.models.calls) == 2
    # second call used the simplified-prompt suffix
    assert "IMPORTANT" in client.models.calls[1]["contents"][0]


def test_malformed_both_times_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, ["still not json", "still not json either"])
    with pytest.raises(ExtractionMalformedOutput):
        extract_invoice([b"fake"])


def test_schema_mismatch_treated_as_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_schema = '{"total_amount": "100.00"}'  # missing required fields
    _patch_client(monkeypatch, [bad_schema, bad_schema])
    with pytest.raises(ExtractionMalformedOutput):
        extract_invoice([b"fake"])


def test_transport_error_raises_hard_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, [ConnectionError("network down")])
    with pytest.raises(ExtractionHardFailure):
        extract_invoice([b"fake"])


def test_missing_api_key_raises_hard_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ExtractionHardFailure):
        extract_invoice([b"fake"])


# ---------------- failover to OpenAI (EXTRACTION_CONTRACT.md "LLM choice") ----------------


def test_below_threshold_does_not_fail_over(monkeypatch: pytest.MonkeyPatch) -> None:
    """2 consecutive Gemini failures (threshold is 3) must NOT touch OpenAI."""
    _patch_client(monkeypatch, [ConnectionError("down"), ConnectionError("down")])
    openai_client = _patch_openai(monkeypatch, [])
    for _ in range(2):
        with pytest.raises(ExtractionHardFailure):
            extract_invoice([b"fake"])
    assert openai_client.calls == []


def test_third_consecutive_failure_fails_over_to_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(
        monkeypatch,
        [ConnectionError("down"), ConnectionError("down"), ConnectionError("down")],
    )
    openai_client = _patch_openai(monkeypatch, [VALID_JSON])

    for _ in range(2):
        with pytest.raises(ExtractionHardFailure):
            extract_invoice([b"fake"])

    extracted, meta = extract_invoice([b"fake"])
    assert extracted.vendor_name == "Sharma Traders"
    assert meta["model"] == "gpt-4o-mini"
    assert len(openai_client.calls) == 1


def test_subsequent_calls_during_outage_skip_gemini_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gemini_client = _patch_client(
        monkeypatch,
        [ConnectionError("down"), ConnectionError("down"), ConnectionError("down")],
    )
    openai_client = _patch_openai(monkeypatch, [VALID_JSON, VALID_JSON])

    for _ in range(2):
        with pytest.raises(ExtractionHardFailure):
            extract_invoice([b"fake"])
    extract_invoice([b"fake"])  # trips failover, 1st OpenAI call
    extract_invoice([b"fake"])  # still within window, 2nd OpenAI call

    assert len(gemini_client.models.calls) == 3  # no 4th attempt at Gemini
    assert len(openai_client.calls) == 2


def test_success_resets_failure_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """2 failures then a success must not carry over into a later run."""
    _patch_client(
        monkeypatch, [ConnectionError("down"), ConnectionError("down"), VALID_JSON]
    )
    openai_client = _patch_openai(monkeypatch, [])
    for _ in range(2):
        with pytest.raises(ExtractionHardFailure):
            extract_invoice([b"fake"])
    extract_invoice([b"fake"])  # succeeds, resets counter

    # One more failure afterward is back to count=1, well under threshold.
    _patch_client(monkeypatch, [ConnectionError("down")])
    with pytest.raises(ExtractionHardFailure):
        extract_invoice([b"fake"])
    assert openai_client.calls == []
