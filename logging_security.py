"""Logging helpers that prevent credentials from being written to local logs."""

from __future__ import annotations

import logging
import re
from typing import Iterable


_PATTERNS = (
    re.compile(r"(Authorization['\"]?\s*[:=]\s*['\"]?Bearer\s+)[^'\"\s,}]+", re.I),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._~+\-/=]+", re.I),
    re.compile(
        r"(['\"]?(?:password|totp|clientcode|api[_-]?key|x-privatekey|x-clientcode)"
        r"['\"]?\s*[:=]\s*['\"]?)([^'\"\s,}]+)",
        re.I,
    ),
    re.compile(r"(https://api\.telegram\.org/bot)[^/\s]+", re.I),
)


def redact_secrets(value: object) -> str:
    """Return a printable value with common broker/Telegram secrets masked."""
    text = str(value)
    for pattern in _PATTERNS:
        text = pattern.sub(r"\1***", text)
    return text


class SecretRedactingFilter(logging.Filter):
    """Redact the fully formatted message before any handler emits it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_secrets(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def install_secret_redaction(handlers: Iterable[logging.Handler] | None = None) -> None:
    """Attach redaction to root handlers and third-party logzero handlers."""
    flt = SecretRedactingFilter()
    root = logging.getLogger()
    root.addFilter(flt)
    for handler in handlers or root.handlers:
        handler.addFilter(flt)
    try:
        import logzero

        logzero.logger.addFilter(flt)
        for handler in logzero.logger.handlers:
            handler.addFilter(flt)
    except Exception:
        pass

