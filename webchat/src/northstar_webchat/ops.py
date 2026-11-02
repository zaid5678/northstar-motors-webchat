"""Operational logs (D-031): one JSON line per event on stderr, so Docker and terminals keep them.

These are for whoever runs the service. Nothing a customer typed or gave is ever logged: no
message text, names, contact details, registrations, or booking references. A session ID is a
cookie, so a credential, and appears only as a short hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

LOGGER = logging.getLogger("northstar_webchat.ops")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        row: dict[str, Any] = {
            "at": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        row.update(getattr(record, "fields", {}))
        return json.dumps(row, default=str)


def configure(*, stream: TextIO | None = None, level: str = "INFO") -> None:
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    LOGGER.handlers = [handler]
    LOGGER.setLevel(level.upper())
    LOGGER.propagate = False


def session_tag(session_id: str) -> str:
    """Enough to follow one conversation through the logs, and nothing to steal."""
    return hashlib.sha256(session_id.encode()).hexdigest()[:12]


def log(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    LOGGER.log(level, event, extra={"fields": fields})


def warn(event: str, **fields: Any) -> None:
    log(event, level=logging.WARNING, **fields)
