"""Durable file publication helpers shared by DockStart workflows.

Every helper writes a sibling temporary file, flushes it to disk and only then
replaces the destination.  A failed write therefore leaves an existing target
untouched instead of exposing a truncated JSON/config/report file.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Durably replace *path* with *payload* without partial publication."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)

        last_error: OSError | None = None
        for attempt in range(6):
            try:
                os.replace(temporary_path, path)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                if attempt == 5:
                    break
                time.sleep(0.02 * (attempt + 1))
        if last_error is not None:
            raise last_error
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def atomic_write_text(path: Path, payload: str, *, encoding: str = "utf-8") -> None:
    """Durably replace a text file using an explicit encoding."""

    atomic_write_bytes(path, payload.encode(encoding))


def atomic_write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    """Serialize JSON deterministically and publish it atomically."""

    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
