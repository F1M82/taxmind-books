"""TaxMind Books — Tally Desktop Connector.

The connector runs on the user's Windows machine alongside TallyPrime.
It opens an outbound WebSocket to the backend, registers itself for
one company via a connector token, and dispatches commands the
backend sends (post_voucher, sync_masters, etc.) to TallyPrime over
its local HTTP+XML interface.

P0.21 (this module's first version) lands the cleaned-up
`tally_client.py` from `salvage/` plus a `config.py`. The WS client,
message dispatcher, and main entry point land in P0.22.
"""

__version__ = "0.1.0"
