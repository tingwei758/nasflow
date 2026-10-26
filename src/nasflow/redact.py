"""Keep secrets out of text a human or a log file will see."""

from __future__ import annotations

import logging
from collections.abc import Iterable

# Shorter values would match ordinary words; real keys and passwords are longer.
_MIN_SECRET_LENGTH = 6
_MASK = "***"


def redact(text: str, secrets: Iterable[str | None]) -> str:
    for secret in secrets:
        if secret and len(secret) >= _MIN_SECRET_LENGTH:
            text = text.replace(secret, _MASK)
    return text


class RedactingFormatter(logging.Formatter):
    """Wraps a formatter so the final line, traceback included, is redacted."""

    def __init__(self, inner: logging.Formatter, secrets: Iterable[str | None]):
        super().__init__()
        self._inner = inner
        self._secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return redact(self._inner.format(record), self._secrets)
