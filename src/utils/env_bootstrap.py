"""
Stdlib-only environment setup for LangChain / LangSmith.

Must run immediately after load_dotenv() and before importing langsmith,
langchain, or langgraph so invalid API keys never enable tracing.
"""

from __future__ import annotations

import os

_PLACEHOLDER_KEYS = frozenset(
    {"your-langsmith-api-key", "changeme", "placeholder"}
)


def bootstrap_langchain_env() -> None:
    # LangSmith onboarding UI exports LANGSMITH_*; LangChain reads LANGCHAIN_*.
    if not os.getenv("LANGCHAIN_API_KEY", "").strip():
        ls_key = os.getenv("LANGSMITH_API_KEY", "").strip()
        if ls_key:
            os.environ["LANGCHAIN_API_KEY"] = ls_key
    if not os.getenv("LANGCHAIN_PROJECT", "").strip():
        ls_proj = os.getenv("LANGSMITH_PROJECT", "").strip()
        if ls_proj:
            os.environ["LANGCHAIN_PROJECT"] = ls_proj
    if not os.getenv("LANGCHAIN_ENDPOINT", "").strip():
        ls_ep = os.getenv("LANGSMITH_ENDPOINT", "").strip()
        if ls_ep:
            os.environ["LANGCHAIN_ENDPOINT"] = ls_ep

    key = os.getenv("LANGCHAIN_API_KEY", "").strip()
    key_lower = key.lower()
    invalid = (not key) or (key_lower in {p.lower() for p in _PLACEHOLDER_KEYS})

    if invalid:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ.pop("LANGCHAIN_API_KEY", None)
        return

    if os.getenv("LANGCHAIN_TRACING_V2", "true").lower() in ("false", "0", "no", "off"):
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
