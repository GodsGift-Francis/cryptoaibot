"""Structured JSON logging with secret redaction.

Every record is a single JSON line. Context such as bot_instance_id, mode,
correlation_id, client_order_id and exchange_order_id is passed via `extra`.
Secrets are scrubbed from both structured fields and message text, including
values that merely *contain* a configured secret.
"""
import json
import logging
import re
import sys
from datetime import datetime, timezone

_SECRET_KEYS = re.compile(r"(api[_-]?key|secret|signature|token|password|authorization)", re.I)
_known_secrets: set[str] = set()
_STANDARD = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


def register_secret(value: str | None) -> None:
    if value and len(value) >= 6:
        _known_secrets.add(value)


def redact(value):
    if isinstance(value, dict):
        return {k: ("***" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for s in _known_secrets:
            if s in value:
                value = value.replace(s, "***")
        return re.sub(r"(signature=)[^&\s\"]+", r"\1***", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _STANDARD and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str)


class ContextAdapter(logging.LoggerAdapter):
    """Merges static context with per-call `extra` (stdlib <3.13 drops call extras)."""

    def process(self, msg, kwargs):
        kwargs["extra"] = {**(self.extra or {}), **(kwargs.get("extra") or {})}
        return msg, kwargs

    def bind(self, **context):
        return ContextAdapter(self.logger, {**(self.extra or {}), **context})


def configure(level: str = "INFO", **static_context) -> logging.Logger:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    logger = logging.getLogger("crypto_bot")
    if static_context:
        return ContextAdapter(logger, static_context)
    return ContextAdapter(logger, {})


def get_logger(name: str = "crypto_bot", **context):
    return ContextAdapter(logging.getLogger(name), context)
