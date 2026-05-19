"""Connector entry point.

Reads settings from env / .env, constructs the TallyClient + WS
client, and runs the reconnect loop.
"""

from __future__ import annotations

import asyncio
import logging

from connector.config import get_settings
from connector.tally_client import TallyClient
from connector.ws_client import ConnectorWSClient


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
    client = ConnectorWSClient(
        ws_url=cfg.BACKEND_WS_URL,
        connector_token=cfg.CONNECTOR_TOKEN.get_secret_value(),
        company_id=company_id,
        tally=tally,
        heartbeat_seconds=cfg.HEARTBEAT_SECONDS,
        initial_backoff=cfg.RECONNECT_INITIAL_BACKOFF,
        max_backoff=cfg.RECONNECT_MAX_BACKOFF,
    )
    await client.run_forever()


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
