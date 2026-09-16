"""First-run interactive enrollment.

Runs when the connector starts with no `CONNECTOR_TOKEN` and stdin is a
real terminal: prompts for the one-time enrollment code an owner issued
(`POST /api/v1/connector/enrollment-codes`, see CLAUDE.md "Connector
enrollment for local dev"), exchanges it for a connector token
(`POST /api/v1/connector/enroll`), and writes the result into the `.env`
file `ConnectorSettings` will read on the next construction -- turning
"run curl commands" into "type the code you were given."

Non-interactive launches (no tty -- a service, a script, a
`Start-Process` with no visible console) skip this entirely and keep
the existing fail-fast behavior; prompting there would hang forever
waiting for input nobody can supply.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

_MAX_ATTEMPTS = 3
_ENROLL_PATH = "/api/v1/connector/enroll"
_WS_TO_HTTP_SCHEME = {"ws": "http", "wss": "https"}


class EnrollmentError(Exception):
    """The backend rejected the code (unknown / expired / already used)."""


class EnrollmentTransportError(Exception):
    """The backend could not be reached at all."""


def derive_http_base(ws_url: str) -> str:
    """``wss://host/api/v1/connector/ws`` -> ``https://host``.

    Swaps the WS scheme for its HTTP equivalent and drops everything
    after the host -- the enroll endpoint's own path is appended
    separately by the caller, never assumed from the WS URL's path shape.
    """
    parts = urlsplit(ws_url)
    scheme = _WS_TO_HTTP_SCHEME.get(parts.scheme, parts.scheme)
    return urlunsplit((scheme, parts.netloc, "", "", ""))


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        message = body.get("error", {}).get("message")
        return str(message) if message else f"HTTP {resp.status_code}"
    except Exception:
        return f"HTTP {resp.status_code}"


async def exchange_code(http_base: str, code: str) -> dict[str, object]:
    """POST the raw code to ``/connector/enroll``.

    Raises ``EnrollmentError`` on a rejected code (expired / consumed /
    unknown) and ``EnrollmentTransportError`` if the backend can't be
    reached at all.
    """
    url = http_base.rstrip("/") + _ENROLL_PATH
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"code": code})
    except httpx.HTTPError as exc:
        raise EnrollmentTransportError(f"could not reach {url}: {exc}") from exc
    if resp.status_code >= 400:
        raise EnrollmentError(_error_message(resp))
    result: dict[str, object] = resp.json()
    return result


def read_env_file(path: Path) -> dict[str, str]:
    """Parse existing ``KEY=VALUE`` lines, ignoring comments/blanks.

    Values are round-tripped verbatim (no un-quoting) -- callers only
    need to preserve whatever was already there for keys this module
    doesn't touch.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def write_env_file(path: Path, updates: dict[str, str]) -> None:
    """Merge ``updates`` into whatever's already at ``path``.

    Never drops an existing key ``updates`` doesn't mention -- a
    re-enrollment must not silently lose a hand-tuned
    ``TALLY_HOST``/``BACKEND_WS_URL`` that was already there.
    """
    values = read_env_file(path)
    values.update(updates)
    path.write_text(
        "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8"
    )


async def run_interactive_enrollment(*, ws_url: str, env_path: Path) -> bool:
    """Prompt for a code, exchange it, and write ``env_path``.

    Returns True on success (the caller should reload settings so the
    freshly-written token takes effect), False if the user gave up
    (blank input, Ctrl+C, or the code was rejected on every attempt) or
    stdin isn't a real terminal to prompt on in the first place.
    """
    if not sys.stdin.isatty():
        return False

    http_base = derive_http_base(ws_url)
    print("No connector token found.")
    print(
        "If an owner has given you a one-time enrollment code, paste it "
        "below (press Enter with nothing typed to exit)."
    )
    try:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            code = input("Enrollment code: ").strip()
            if not code:
                return False
            try:
                result = await exchange_code(http_base, code)
            except EnrollmentTransportError as exc:
                print(f"Could not reach the backend: {exc}")
                return False
            except EnrollmentError as exc:
                remaining = _MAX_ATTEMPTS - attempt
                if remaining <= 0:
                    print(f"Code rejected: {exc}")
                    return False
                print(f"Code rejected: {exc} ({remaining} attempt(s) left)")
                continue

            write_env_file(
                env_path,
                {
                    "CONNECTOR_TOKEN": str(result["connector_token"]),
                    "CONNECTOR_COMPANY_ID": str(result["company_id"]),
                },
            )
            print(
                f"Enrolled. Token written to {env_path} "
                f"(valid {result.get('expires_in_days', 365)} days)."
            )
            return True
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return False
    return False
