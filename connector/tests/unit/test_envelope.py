"""Unit tests for envelope.py."""

from __future__ import annotations

import json

import pytest
from connector.envelope import (
    PROTOCOL_VERSION,
    ProtocolError,
    build_envelope,
    parse_envelope,
)


def test_build_envelope_includes_required_fields() -> None:
    raw = build_envelope(type_="heartbeat", payload={"x": 1})
    env = json.loads(raw)
    for k in ("type", "request_id", "ts", "payload"):
        assert k in env
    assert env["type"] == "heartbeat"
    assert env["payload"] == {"x": 1}


def test_protocol_version_is_one() -> None:
    assert PROTOCOL_VERSION == 1


def test_parse_envelope_happy_path() -> None:
    raw = build_envelope(type_="ping", payload={})
    env = parse_envelope(raw)
    assert env["type"] == "ping"


def test_parse_envelope_rejects_invalid_json() -> None:
    with pytest.raises(ProtocolError):
        parse_envelope("not json")


def test_parse_envelope_rejects_missing_field() -> None:
    raw = json.dumps({"type": "x", "request_id": "y", "ts": "z"})  # no payload
    with pytest.raises(ProtocolError):
        parse_envelope(raw)


def test_parse_envelope_rejects_non_object_payload() -> None:
    raw = json.dumps(
        {
            "type": "x",
            "request_id": "y",
            "ts": "z",
            "payload": "not an object",
        }
    )
    with pytest.raises(ProtocolError):
        parse_envelope(raw)


def test_parse_envelope_rejects_oversize_message() -> None:
    huge = "x" * 1_000_001
    raw = json.dumps(
        {"type": "x", "request_id": "y", "ts": "z", "payload": {"big": huge}}
    )
    with pytest.raises(ProtocolError):
        parse_envelope(raw)
