"""Object storage for uploaded ingestion files.

Local disk today (pilot has no S3 bucket provisioned — see CLAUDE.md
"Phase 1 scope"). The key-based interface matches what an S3-backed
implementation would offer, so swapping later touches only this module.
"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings


def _root() -> Path:
    root = Path(get_settings().STORAGE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_path(key: str) -> Path:
    """Resolve `key` under the storage root, rejecting path escape."""
    root = _root().resolve()
    path = (root / key).resolve()
    if root not in path.parents and path != root:
        raise ValueError(f"storage key escapes root: {key!r}")
    return path


def save_bytes(key: str, content: bytes) -> None:
    path = _safe_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def read_bytes(key: str) -> bytes:
    return _safe_path(key).read_bytes()


def exists(key: str) -> bool:
    return _safe_path(key).exists()
