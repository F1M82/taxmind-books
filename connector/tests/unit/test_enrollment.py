"""Unit tests for connector/enrollment.py — the first-run interactive
enrollment prompt (turns "run curl commands" into "type the code you
were given")."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from connector.enrollment import (
    EnrollmentError,
    EnrollmentTransportError,
    derive_http_base,
    exchange_code,
    read_env_file,
    run_interactive_enrollment,
    write_env_file,
)

# ---------------- derive_http_base ----------------


def test_derive_http_base_production_wss() -> None:
    assert (
        derive_http_base("wss://books.gcwealthguru.com/api/v1/connector/ws")
        == "https://books.gcwealthguru.com"
    )


def test_derive_http_base_local_ws_preserves_port() -> None:
    assert (
        derive_http_base("ws://localhost:8000/api/v1/connector/ws")
        == "http://localhost:8000"
    )


# ---------------- read_env_file / write_env_file ----------------


def test_read_env_file_missing_returns_empty(tmp_path: Path) -> None:
    assert read_env_file(tmp_path / "nope.env") == {}


def test_read_env_file_ignores_comments_and_blanks(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text(
        "# a comment\n\nTALLY_HOST=localhost\nTALLY_PORT=9000\n", encoding="utf-8"
    )
    assert read_env_file(p) == {"TALLY_HOST": "localhost", "TALLY_PORT": "9000"}


def test_write_env_file_creates_new_file(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    write_env_file(p, {"CONNECTOR_TOKEN": "tok", "CONNECTOR_COMPANY_ID": "co-1"})
    assert read_env_file(p) == {
        "CONNECTOR_TOKEN": "tok",
        "CONNECTOR_COMPANY_ID": "co-1",
    }


def test_write_env_file_preserves_unrelated_existing_keys(tmp_path: Path) -> None:
    # The exact scenario a connector rebuild/re-enrollment must not
    # repeat: a hand-tuned TALLY_HOST/BACKEND_WS_URL surviving a token
    # refresh, not getting silently dropped.
    p = tmp_path / ".env"
    p.write_text(
        "BACKEND_WS_URL=ws://localhost:8000/api/v1/connector/ws\n"
        "TALLY_HOST=localhost\n"
        "TALLY_PORT=9000\n",
        encoding="utf-8",
    )
    write_env_file(p, {"CONNECTOR_TOKEN": "newtok", "CONNECTOR_COMPANY_ID": "co-1"})
    values = read_env_file(p)
    assert values["BACKEND_WS_URL"] == "ws://localhost:8000/api/v1/connector/ws"
    assert values["TALLY_HOST"] == "localhost"
    assert values["TALLY_PORT"] == "9000"
    assert values["CONNECTOR_TOKEN"] == "newtok"
    assert values["CONNECTOR_COMPANY_ID"] == "co-1"


def test_write_env_file_overwrites_stale_token(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    write_env_file(p, {"CONNECTOR_TOKEN": "old"})
    write_env_file(p, {"CONNECTOR_TOKEN": "new"})
    assert read_env_file(p)["CONNECTOR_TOKEN"] == "new"


# ---------------- exchange_code ----------------


@pytest.mark.asyncio
async def test_exchange_code_success(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:8000/api/v1/connector/enroll",
        method="POST",
        json={
            "connector_id": "c-1",
            "company_id": "co-1",
            "connector_token": "jwt-abc",
            "expires_in_days": 365,
        },
    )
    result = await exchange_code("http://localhost:8000", "the-code")
    assert result["connector_token"] == "jwt-abc"
    assert result["company_id"] == "co-1"


@pytest.mark.asyncio
async def test_exchange_code_rejected_raises_enrollment_error(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url="http://localhost:8000/api/v1/connector/enroll",
        method="POST",
        status_code=404,
        json={"error": {"code": "enrollment_code_not_found", "message": "not found"}},
    )
    with pytest.raises(EnrollmentError, match="not found"):
        await exchange_code("http://localhost:8000", "bad-code")


@pytest.mark.asyncio
async def test_exchange_code_unreachable_raises_transport_error(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(EnrollmentTransportError):
        await exchange_code("http://localhost:8000", "any-code")


# ---------------- run_interactive_enrollment ----------------


@pytest.mark.asyncio
async def test_run_interactive_enrollment_skips_when_not_a_tty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    ok = await run_interactive_enrollment(
        ws_url="ws://localhost:8000/api/v1/connector/ws", env_path=tmp_path / ".env"
    )
    assert ok is False
    assert not (tmp_path / ".env").exists()


@pytest.mark.asyncio
async def test_run_interactive_enrollment_blank_input_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    ok = await run_interactive_enrollment(
        ws_url="ws://localhost:8000/api/v1/connector/ws", env_path=tmp_path / ".env"
    )
    assert ok is False


@pytest.mark.asyncio
async def test_run_interactive_enrollment_writes_env_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "good-code")
    httpx_mock.add_response(
        url="http://localhost:8000/api/v1/connector/enroll",
        method="POST",
        json={
            "connector_id": "c-1",
            "company_id": "co-1",
            "connector_token": "jwt-abc",
            "expires_in_days": 365,
        },
    )
    env_path = tmp_path / ".env"
    ok = await run_interactive_enrollment(
        ws_url="ws://localhost:8000/api/v1/connector/ws", env_path=env_path
    )
    assert ok is True
    values = read_env_file(env_path)
    assert values["CONNECTOR_TOKEN"] == "jwt-abc"
    assert values["CONNECTOR_COMPANY_ID"] == "co-1"


@pytest.mark.asyncio
async def test_run_interactive_enrollment_retries_then_gives_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    attempts = iter(["bad-1", "bad-2", "bad-3"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(attempts))
    for _ in range(3):
        httpx_mock.add_response(
            url="http://localhost:8000/api/v1/connector/enroll",
            method="POST",
            status_code=404,
            json={"error": {"code": "enrollment_code_not_found", "message": "nope"}},
        )
    env_path = tmp_path / ".env"
    ok = await run_interactive_enrollment(
        ws_url="ws://localhost:8000/api/v1/connector/ws", env_path=env_path
    )
    assert ok is False
    assert not env_path.exists()


@pytest.mark.asyncio
async def test_run_interactive_enrollment_transport_failure_stops_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "any-code")
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    ok = await run_interactive_enrollment(
        ws_url="ws://localhost:8000/api/v1/connector/ws",
        env_path=tmp_path / ".env",
    )
    assert ok is False
