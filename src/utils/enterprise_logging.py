"""Structured JSON logging and optional AWS X-Ray for Lambda."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Mapping

from src.utils.request_context import get_correlation_id


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line; includes correlation_id when set."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        cid = get_correlation_id()
        if cid:
            payload["correlation_id"] = cid
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, Mapping):
            payload.update(extra)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Call once at process start (Lambda cold start or uvicorn)."""
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    fmt = os.getenv("LOG_FORMAT", "text").lower()
    if fmt == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(level)


def maybe_patch_xray() -> None:
    """No-op if SDK missing or tracing disabled."""
    if os.getenv("XRAY_PATCH_SDK", "").lower() not in ("1", "true", "yes"):
        if os.getenv("AWS_XRAY_TRACING_ENABLED", "").lower() != "true":
            return
    try:
        from aws_xray_sdk.core import patch_all

        patch_all()
        logging.getLogger(__name__).info("aws_xray_sdk patch_all applied")
    except ImportError:
        logging.getLogger(__name__).warning(
            "AWS_XRAY_TRACING_ENABLED set but aws-xray-sdk not installed "
            "(pip install -r requirements-optional.txt)"
        )
