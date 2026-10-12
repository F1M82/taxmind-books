"""Unit tests for invoice_extractor, mocking the Gemini client entirely.

No network call ever happens here -- `_client()` is monkeypatched to
return a fake with a canned `.text` response, per the retry policy in
docs/EXTRACTION_CONTRACT.md.
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


class _FakeClient:
    def __init__(self, replies: list[str | Exception]) -> None:
        self.models = _FakeModels(replies)


def _patch_client(monkeypatch, replies: list[str | Exception]) -> _FakeClient:  # type: ignore[no-untyped-def]
    client = _FakeClient(replies)
    monkeypatch.setattr(invoice_extractor, "_client", lambda: client)
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
