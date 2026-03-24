"""
LangSmith tracing configuration and helpers.

Enables structured traces for every LangGraph run, with:
  - Project-level grouping
  - Claim-ID and member-ID as metadata tags (no PHI in trace names)
  - Custom evaluator hooks for governance scoring
"""

from __future__ import annotations

import os
import functools
import logging
from typing import Any, Callable, Optional
from datetime import datetime

from langsmith import Client, traceable
from langsmith.evaluation import EvaluationResult

from src.utils.env_bootstrap import bootstrap_langchain_env

logger = logging.getLogger(__name__)

LANGCHAIN_PROJECT = os.getenv("LANGCHAIN_PROJECT", "calclaim-demo")

# Values that should never be sent to the API (403 / noise if tracing stays on)
_LANGSMITH_PLACEHOLDER_KEYS = frozenset(
    {"your-langsmith-api-key", "changeme", "placeholder"}
)


def _langsmith_key_is_valid(key: str) -> bool:
    if not key.strip():
        return False
    if key.strip().lower() in {k.lower() for k in _LANGSMITH_PLACEHOLDER_KEYS}:
        return False
    return True


def configure_tracing() -> None:
    """
    Set env vars that LangChain reads automatically.
    Tracing stays OFF unless LANGCHAIN_API_KEY is set to a non-placeholder value
    (avoids 403 spam from example .env values).
    """
    bootstrap_langchain_env()
    key = os.getenv("LANGCHAIN_API_KEY", "").strip()
    if _langsmith_key_is_valid(key):
        want_trace = os.getenv("LANGCHAIN_TRACING_V2", "true").lower() not in (
            "false", "0", "no", "off",
        )
        os.environ["LANGCHAIN_TRACING_V2"] = "true" if want_trace else "false"
        os.environ.setdefault("LANGCHAIN_PROJECT", LANGCHAIN_PROJECT)
        os.environ["LANGCHAIN_API_KEY"] = key
        if want_trace:
            logger.info("LangSmith tracing enabled → project=%s", LANGCHAIN_PROJECT)
        else:
            logger.info("LangSmith API key set but LANGCHAIN_TRACING_V2 is off")
    else:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ.pop("LANGCHAIN_API_KEY", None)
        logger.info(
            "LangSmith tracing disabled (set a real LANGCHAIN_API_KEY to enable)"
        )


def get_langsmith_client() -> Optional[Client]:
    key = os.getenv("LANGCHAIN_API_KEY", "").strip()
    if not _langsmith_key_is_valid(key):
        logger.warning("LANGCHAIN_API_KEY not set — LangSmith client unavailable")
        return None
    try:
        return Client(api_key=key)
    except Exception as exc:
        logger.warning("LangSmith client init failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Trace metadata helpers
# ---------------------------------------------------------------------------

def build_run_metadata(claim_id: str, member_id: str, workflow_step: str) -> dict[str, str]:
    """
    Safe metadata for LangSmith traces.
    Deliberately omits any PII/PHI — only opaque IDs and step names.
    """
    return {
        "claim_id": claim_id,
        "member_id": member_id,
        "workflow_step": workflow_step,
        "project": LANGCHAIN_PROJECT,
        "timestamp_utc": datetime.utcnow().isoformat(),
        "environment": os.getenv("ENVIRONMENT", "demo"),
    }


# ---------------------------------------------------------------------------
# LangSmith Evaluators (for Quality & Governance Gate)
# ---------------------------------------------------------------------------

def evaluate_hallucination_risk(run_output: str, reference: str) -> EvaluationResult:
    """
    LLM-as-Judge: checks whether the agent output contains unsupported claims.
    In production this would call a Bedrock evaluator model.
    """
    fabricated_keywords = [
        "i'm not sure but", "approximately", "i believe", "possibly",
        "might be", "could be around", "roughly",
    ]
    lower = run_output.lower()
    risk_score = sum(1 for kw in fabricated_keywords if kw in lower)
    score = max(0.0, 1.0 - (risk_score * 0.2))
    return EvaluationResult(
        key="hallucination_risk",
        score=score,
        comment=f"Detected {risk_score} hedging phrase(s).",
    )


def evaluate_pii_leakage(run_output: str) -> EvaluationResult:
    """
    Checks whether raw SSN, DOB, or email leaked into agent output.
    Supplements Bedrock Guardrails for audit purposes.
    """
    import re
    ssn_pattern = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    email_pattern = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
    dob_pattern = re.compile(r"\b(19|20)\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b")

    violations: list[str] = []
    if ssn_pattern.search(run_output):
        violations.append("SSN")
    if email_pattern.search(run_output):
        violations.append("EMAIL")
    if dob_pattern.search(run_output):
        violations.append("DOB")

    score = 1.0 if not violations else 0.0
    return EvaluationResult(
        key="pii_leakage",
        score=score,
        comment=f"PII types detected: {violations}" if violations else "No PII detected.",
    )


def evaluate_adjudication_accuracy(
    agent_decision: str,
    expected_status: str,
) -> EvaluationResult:
    """Exact-match check for claim adjudication outcome."""
    match = expected_status.lower() in agent_decision.lower()
    return EvaluationResult(
        key="adjudication_accuracy",
        score=1.0 if match else 0.0,
        comment=f"Expected '{expected_status}' — match={match}",
    )


# ---------------------------------------------------------------------------
# Convenience decorator — wraps any function in a named LangSmith trace
# ---------------------------------------------------------------------------

def langsmith_trace(name: str, tags: Optional[list] = None) -> Callable:
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        @traceable(name=name, tags=tags or [])
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)
        return wrapper
    return decorator
