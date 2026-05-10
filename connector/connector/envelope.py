"""Wire-protocol envelope per CONNECTOR_PROTOCOL.md §"Message envelope"."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

PROTOCOL_VERSION = 1
MAX_MESSAGE_SIZE_BYTES = 1_000_000


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_envelope(
    *,
    type_: str,
    payload: dict[str, Any],
    request_id: UUID | None = None,
) -> str:
    """JSON-encode an outbound message in the canonical envelope shape."""
    env = {
        "type": type_,
        "request_id": str(request_id or uuid4()),
        "ts": now_iso(),
        "payload": payload,
    }
    return json.dumps(env, separators=(",", ":"))


class ProtocolError(Exception):
    """Raised when an inbound message can't be parsed against the envelope."""


def parse_envelope(raw: str | bytes) -> dict[str, Any]:
    """Validate the envelope shape and return the parsed dict.

    Wire protocol §"Message envelope": every message has type,
    request_id, ts, payload. Missing → drop with a protocol_error.
    """
    if isinstance(raw, bytes):
        if len(raw) > MAX_MESSAGE_SIZE_BYTES:
            raise ProtocolError(
                f"message exceeds {MAX_MESSAGE_SIZE_BYTES} bytes"
            )
        raw = raw.decode("utf-8")
    elif len(raw.encode("utf-8")) > MAX_MESSAGE_SIZE_BYTES:
        raise ProtocolError(
            f"message exceeds {MAX_MESSAGE_SIZE_BYTES} bytes"
        )
    try:
        env = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(env, dict):
        raise ProtocolError("envelope must be a JSON object")
    for required in ("type", "request_id", "ts", "payload"):
        if required not in env:
            raise ProtocolError(f"missing field: {required}")
    if not isinstance(env["payload"], dict):
        raise ProtocolError("payload must be an object")
    return env
