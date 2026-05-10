"""Unit tests for app.core.idempotency helpers (no DB / no HTTP)."""

from __future__ import annotations

from app.core.idempotency import canonical_hash, is_valid_key


# ---------------- is_valid_key ----------------


def test_is_valid_key_accepts_uuid_v4() -> None:
    assert is_valid_key("3a52a9c8-1c3a-4c4a-a1a8-2dc6c4e9c2f1") is True


def test_is_valid_key_rejects_too_short() -> None:
    assert is_valid_key("abc") is False
    assert is_valid_key("a" * 7) is False


def test_is_valid_key_accepts_min_length() -> None:
    assert is_valid_key("a" * 8) is True


def test_is_valid_key_accepts_max_length() -> None:
    assert is_valid_key("a" * 255) is True
    assert is_valid_key("a" * 256) is False


def test_is_valid_key_rejects_whitespace() -> None:
    assert is_valid_key("with space") is False
    assert is_valid_key("with\ttab") is False
    assert is_valid_key("with\nnewline") is False


def test_is_valid_key_rejects_non_ascii() -> None:
    assert is_valid_key("résumé-1234") is False  # é is non-ASCII
    assert is_valid_key("emoji🎉key") is False


def test_is_valid_key_rejects_unprintable() -> None:
    assert is_valid_key("ctrl\x01char") is False


# ---------------- canonical_hash ----------------


def test_canonical_hash_empty_body() -> None:
    h = canonical_hash(b"")
    # Equivalent to hashing "{}"
    assert h == canonical_hash(b"{}")


def test_canonical_hash_key_order_irrelevant() -> None:
    a = canonical_hash(b'{"a":1,"b":2}')
    b = canonical_hash(b'{"b":2,"a":1}')
    assert a == b


def test_canonical_hash_whitespace_irrelevant() -> None:
    a = canonical_hash(b'{"a":1,"b":2}')
    b = canonical_hash(b'{ "a" : 1 , "b" : 2 }')
    assert a == b


def test_canonical_hash_distinct_for_different_values() -> None:
    a = canonical_hash(b'{"amount":"100.00"}')
    b = canonical_hash(b'{"amount":"200.00"}')
    assert a != b


def test_canonical_hash_handles_nested() -> None:
    a = canonical_hash(b'{"x":{"a":1,"b":2}}')
    b = canonical_hash(b'{"x":{"b":2,"a":1}}')
    assert a == b


def test_canonical_hash_falls_back_for_non_json() -> None:
    """Multipart upload bytes should hash deterministically by raw bytes."""
    raw = b"--boundary\r\nfile contents\r\n--boundary--"
    h1 = canonical_hash(raw)
    h2 = canonical_hash(raw)
    assert h1 == h2
    # And differs from JSON of the same logical content.
    assert h1 != canonical_hash(b'{"x":"y"}')


def test_canonical_hash_is_hex_64() -> None:
    """Result is a 64-char hex string (SHA-256)."""
    h = canonical_hash(b'{"a":1}')
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
