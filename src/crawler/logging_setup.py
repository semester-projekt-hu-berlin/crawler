"""Structured logging: every record carries an event name plus key/value fields.

Use ``log_event(logger, "event.name", key=value, ...)``. Output is either
logfmt-style text (default, readable in a terminal) or one JSON object per line
(``--log-format json``, convenient in CI logs).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, event, extra={"fields": fields})


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    fields = getattr(record, "fields", None)
    return dict(fields) if isinstance(fields, dict) else {}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(_fields(record))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class LogfmtFormatter(logging.Formatter):
    @staticmethod
    def _fmt_value(value: Any) -> str:
        text = str(value)
        if text == "" or any(c in text for c in ' "='):
            return json.dumps(text, ensure_ascii=False)
        return text

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, UTC).strftime("%H:%M:%S")
        parts = [f"{ts} {record.levelname:<5} {record.getMessage()}"]
        parts += [f"{k}={self._fmt_value(v)}" for k, v in _fields(record).items()]
        line = " ".join(parts)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else LogfmtFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Third-party libraries are noisy at DEBUG.
    for name in ("urllib3", "google", "charset_normalizer"):
        logging.getLogger(name).setLevel(logging.WARNING)
