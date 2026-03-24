"""Request-scoped context (correlation ID) for structured logs and tracing."""

from __future__ import annotations

import contextvars
from typing import Optional

_correlation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)


def get_correlation_id() -> Optional[str]:
    return _correlation_id.get()


def set_correlation_id(value: Optional[str]) -> contextvars.Token:
    return _correlation_id.set(value)


def reset_correlation_id(token: contextvars.Token) -> None:
    _correlation_id.reset(token)
