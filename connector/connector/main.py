"""Connector entry point.

Reads settings from env / .env, constructs the TallyClient + WS
client, and runs the reconnect loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from connector.config import get_settings
from connector.idempotency_cache import IdempotencyCache
from connector.tally_client import TallyClient
from connector.ws_client import ConnectorWSClient

logger = logging.getLogger("connector.main")

# Steady-state cache housekeeping cadence. Startup cleanup (in
# `_async_main`) covers the "connector offline for weeks, now starting"
# case; this loop covers a long-running process.
_DAILY_CLEANUP_SECONDS = 24 * 60 * 60


async def _daily_cleanup_loop(cache: IdempotencyCache) -> None:
    """Sweep stale idempotency-cache rows once a day for the process life."""
    while True:
        await asyncio.sleep(_DAILY_CLEANUP_SECONDS)
        removed = await asyncio.to_thread(cache.cleanup_stale)
        logger.info(
            "idempotency cache daily cleanup removed %d row(s)", removed
        )


async def _async_main() -> None:
    cfg = get_settings()
    logging.basicConfig(level=cfg.LOG_LEVEL)
    if cfg.CONNECTOR_TOKEN is None:
        raise SystemExit(
            "CONNECTOR_TOKEN missing — run the enrollment flow first."
        )

    # The token JWT carries `company_id`; for now the runtime callers
    # also pass it explicitly via ConnectorSettings (env or `.env`
    # next to the .exe). Phase-0 keeps the flow simple.
    company_id = cfg.CONNECTOR_COMPANY_ID
    if not company_id:
        raise SystemExit(
            "CONNECTOR_COMPANY_ID missing — set after enrollment."
        )

    tally = TallyClient(
        host=cfg.TALLY_HOST,
        port=cfg.TALLY_PORT,
        timeout=cfg.TALLY_TIMEOUT_SECONDS,
    )

    # Process-singleton idempotency cache (BUG-003 prep). Created at the
    # platform AppData/XDG_DATA path; lives for the connector's lifetime.
    cache = IdempotencyCache()
    removed = cache.cleanup_stale()
    logger.info(
        "idempotency cache startup cleanup removed %d row(s)", removed
    )

    client = ConnectorWSClient(
        ws_url=cfg.BACKEND_WS_URL,
        connector_token=cfg.CONNECTOR_TOKEN.get_secret_value(),
        company_id=company_id,
        tally=tally,
        heartbeat_seconds=cfg.HEARTBEAT_SECONDS,
        initial_backoff=cfg.RECONNECT_INITIAL_BACKOFF,
        max_backoff=cfg.RECONNECT_MAX_BACKOFF,
        cache=cache,
    )

    cleanup_task = asyncio.create_task(_daily_cleanup_loop(cache))
    try:
        await client.run_forever()
    finally:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        cache.close()


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
