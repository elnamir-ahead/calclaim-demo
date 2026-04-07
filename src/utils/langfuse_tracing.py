"""
Optional Langfuse tracing for LangGraph / LangChain (dual observability with LangSmith).

Set LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY (optional LANGFUSE_HOST for self-hosted).
Set LANGFUSE_TRACING=false to disable even when keys are present.

Langfuse reads credentials from environment; LangSmith continues to use LANGCHAIN_* when enabled.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from src.utils.langsmith_config import build_langsmith_tracer_callback

logger = logging.getLogger(__name__)


def langfuse_tracing_enabled() -> bool:
    if os.getenv("LANGFUSE_TRACING", "true").lower() in ("false", "0", "no", "off"):
        return False
    pk = (os.getenv("LANGFUSE_PUBLIC_KEY") or "").strip()
    sk = (os.getenv("LANGFUSE_SECRET_KEY") or "").strip()
    bad = {"changeme", "placeholder", "your-langfuse-public-key"}
    if pk.lower() in bad or not pk or not sk:
        return False
    return True


def build_langfuse_callback() -> Optional[Any]:
    """Return LangChain CallbackHandler for Langfuse, or None if disabled/unavailable."""
    if not langfuse_tracing_enabled():
        return None
    try:
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.debug("langfuse package not installed — skipping Langfuse callback")
        return None
    try:
        return CallbackHandler()
    except Exception as exc:
        logger.warning("Langfuse CallbackHandler failed: %s", exc)
        return None


def build_graph_callbacks() -> list[Any]:
    """Callbacks for ``graph.ainvoke`` — LangSmith tracer + optional Langfuse."""
    out: list[Any] = []
    ls = build_langsmith_tracer_callback()
    if ls is not None:
        out.append(ls)
    cb = build_langfuse_callback()
    if cb is not None:
        out.append(cb)
    return out
