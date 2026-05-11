"""Re-export shared DB / HTTP fixtures into the integration tier."""

from __future__ import annotations

# Star-import covers the public fixtures; the autouse fixture has a
# leading underscore (Python convention for "private"), so it must be
# imported explicitly or `from X import *` will skip it.
from tests._db_fixtures import *  # noqa: F403
from tests._db_fixtures import _reset_tenancy_tables  # noqa: F401
