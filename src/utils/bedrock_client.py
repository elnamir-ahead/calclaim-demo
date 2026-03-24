"""
Amazon Bedrock + AgentCore client utilities.

Provides:
  - ChatBedrock wrappers for Claude Sonnet & Haiku via langchain-aws
  - ModelRouter: latency/cost-aware model selection
  - BedrockGuardrailWrapper: apply Bedrock Guardrails inline
  - AgentCoreClient: invoke Bedrock AgentCore endpoints
"""

from __future__ import annotations

import os
import re
import json
import time
import logging
from typing import Any, Literal, Optional

import boto3
from botocore.config import Config
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)

REGION = os.getenv("BEDROCK_REGION", "us-east-1")

# invoke_agent rejects IDs with hyphens / length > 10 (see ValidationException in demo logs).
_BEDROCK_INVOKE_ID_RE = re.compile(r"^[0-9a-zA-Z]{1,10}$")

_guardrail_placeholder_warned = False
_agentcore_mock_warned = False


def _effective_guardrail_id() -> str:
    """
    Return guardrail ID for Converse / ApplyGuardrail, or "" if unset or placeholder
    (e.g. your-guardrail-id from .env.example) so local runs do not hit ValidationException.
    """
    global _guardrail_placeholder_warned
    raw = os.getenv("BEDROCK_GUARDRAIL_ID", "").strip()
    if not raw:
        return ""
    rl = raw.lower()
    if rl.startswith("your-") or rl in frozenset({"changeme", "placeholder"}):
        if not _guardrail_placeholder_warned:
            logger.warning(
                "BEDROCK_GUARDRAIL_ID is placeholder-like (%r) — skipping guardrail on Bedrock calls",
                raw[:48],
            )
            _guardrail_placeholder_warned = True
        return ""
    return raw


def _guardrail_version_str() -> str:
    return (os.getenv("BEDROCK_GUARDRAIL_VERSION", "DRAFT") or "DRAFT").strip()


def _invoke_agent_ids_valid(agent_id: str, alias_id: str) -> bool:
    a = (agent_id or "").strip()
    b = (alias_id or "").strip()
    return bool(_BEDROCK_INVOKE_ID_RE.match(a) and _BEDROCK_INVOKE_ID_RE.match(b))

_RETRY_CONFIG = Config(
    region_name=REGION,
    retries={"max_attempts": 3, "mode": "adaptive"},
)

ModelTier = Literal["fast", "balanced", "powerful"]


def _bedrock_runtime() -> boto3.client:
    return boto3.client("bedrock-runtime", config=_RETRY_CONFIG)


def _bedrock_agent_runtime() -> boto3.client:
    return boto3.client("bedrock-agent-runtime", config=_RETRY_CONFIG)


# ---------------------------------------------------------------------------
# LangChain ChatBedrock instances
# ---------------------------------------------------------------------------

def get_claude_sonnet() -> ChatBedrockConverse:
    """Claude 3.5 Sonnet — complex reasoning, orchestration."""
    kwargs: dict[str, Any] = {
        "model_id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "region_name": REGION,
        "max_tokens": 4096,
        "temperature": 0.0,
    }
    gid = _effective_guardrail_id()
    if gid:
        kwargs["guardrail_config"] = {
            "guardrailIdentifier": gid,
            "guardrailVersion": _guardrail_version_str(),
            "trace": "enabled",
        }
    return ChatBedrockConverse(**kwargs)


def get_claude_haiku() -> ChatBedrockConverse:
    """Claude 3 Haiku — fast, low-cost for simple lookups."""
    kwargs: dict[str, Any] = {
        "model_id": "anthropic.claude-3-haiku-20240307-v1:0",
        "region_name": REGION,
        "max_tokens": 1024,
        "temperature": 0.0,
    }
    gid = _effective_guardrail_id()
    if gid:
        kwargs["guardrail_config"] = {
            "guardrailIdentifier": gid,
            "guardrailVersion": _guardrail_version_str(),
            "trace": "enabled",
        }
    return ChatBedrockConverse(**kwargs)


# ---------------------------------------------------------------------------
# Model Router
# ---------------------------------------------------------------------------

class ModelRouter:
    """
    Cost-vs-capability latency-aware router.

    Rules (mirrors the architecture diagram):
      - 'fast':     Haiku — simple lookups, DUR checks, status queries
      - 'balanced': Sonnet — adjudication reasoning, formulary decisions
      - 'powerful': Sonnet — compliance analysis, multi-step PA evaluation
    """

    ROUTING_TABLE: dict[str, ModelTier] = {
        "claim_status_lookup": "fast",
        "dur_check": "fast",
        "formulary_lookup": "fast",
        "refill_too_soon": "fast",
        "copay_calculation": "balanced",
        "pa_evaluation": "balanced",
        "adjudication": "balanced",
        "compliance_review": "powerful",
        "audit_summary": "powerful",
        "supervisor_routing": "fast",
    }

    def __init__(self) -> None:
        self._sonnet = get_claude_sonnet()
        self._haiku = get_claude_haiku()

    def route(self, task: str) -> ChatBedrockConverse:
        tier: ModelTier = self.ROUTING_TABLE.get(task, "balanced")
        if tier == "fast":
            logger.debug("ModelRouter → Haiku (task=%s)", task)
            return self._haiku
        logger.debug("ModelRouter → Sonnet (task=%s, tier=%s)", task, tier)
        return self._sonnet


# ---------------------------------------------------------------------------
# Bedrock Guardrail inline check
# ---------------------------------------------------------------------------

class BedrockGuardrailChecker:
    """
    Apply Bedrock Guardrails to free-text for PII/PHI detection
    before the text is logged or returned to the caller.
    Falls back gracefully when guardrail ID is not configured.
    """

    def __init__(self) -> None:
        self._client = _bedrock_runtime()
        self._gid = _effective_guardrail_id()
        self._gversion = _guardrail_version_str()
        self._enabled = bool(self._gid)

    def check(self, text: str, source: str = "OUTPUT") -> dict[str, Any]:
        if not self._enabled:
            return {"action": "NONE", "text": text, "assessments": []}
        try:
            resp = self._client.apply_guardrail(
                guardrailIdentifier=self._gid,
                guardrailVersion=self._gversion,
                source=source,
                content=[{"text": {"text": text}}],
            )
            action = resp.get("action", "NONE")
            if action == "GUARDRAIL_INTERVENED":
                cleaned = " ".join(
                    o.get("text", {}).get("text", "")
                    for o in resp.get("output", [])
                )
                return {"action": action, "text": cleaned, "assessments": resp.get("assessments", [])}
            return {"action": action, "text": text, "assessments": []}
        except Exception as exc:
            logger.warning("Guardrail check failed: %s", exc)
            return {"action": "ERROR", "text": text, "assessments": []}


# ---------------------------------------------------------------------------
# AgentCore client
# ---------------------------------------------------------------------------

class AgentCoreClient:
    """
    Invoke an Amazon Bedrock AgentCore agent (bedrock-agent-runtime).

    AgentCore is used for:
      - CalcClaim adjudication tool server
      - Formulary server
      - Knowledge server (Confluence / policy documents)
    """

    def __init__(self, agent_id: Optional[str] = None, alias_id: Optional[str] = None) -> None:
        self._client = _bedrock_agent_runtime()
        self.agent_id = agent_id or os.getenv("AGENTCORE_AGENT_ID", "")
        self.alias_id = alias_id or os.getenv("AGENTCORE_AGENT_ALIAS_ID", "TSTALIASID")

    def invoke(
        self,
        session_id: str,
        input_text: str,
        session_attributes: Optional[dict] = None,
        prompt_attributes: Optional[dict] = None,
    ) -> dict[str, Any]:
        global _agentcore_mock_warned
        if not _invoke_agent_ids_valid(self.agent_id, self.alias_id):
            if not _agentcore_mock_warned:
                logger.warning(
                    "AgentCoreClient: AGENTCORE_AGENT_ID / AGENTCORE_AGENT_ALIAS_ID missing or "
                    "invalid for invoke_agent (need 1–10 alphanumeric chars) — using mock response"
                )
                _agentcore_mock_warned = True
            return self._mock_response(input_text)

        kwargs: dict[str, Any] = {
            "agentId": self.agent_id,
            "agentAliasId": self.alias_id,
            "sessionId": session_id,
            "inputText": input_text,
        }
        if session_attributes:
            kwargs["sessionState"] = {"sessionAttributes": session_attributes}

        start = time.monotonic()
        completion = ""
        trace_steps = []

        try:
            response = self._client.invoke_agent(**kwargs)
            for event in response.get("completion", []):
                if "chunk" in event:
                    completion += event["chunk"].get("bytes", b"").decode("utf-8")
                if "trace" in event:
                    trace_steps.append(event["trace"])
        except Exception as exc:
            logger.error("AgentCore invocation error: %s", exc)
            raise

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "completion": completion,
            "trace": trace_steps,
            "elapsed_ms": elapsed_ms,
            "session_id": session_id,
        }

    @staticmethod
    def _mock_response(input_text: str) -> dict[str, Any]:
        """Demo fallback when AgentCore is not deployed."""
        return {
            "completion": f"[MOCK AgentCore] Processed: {input_text[:80]}",
            "trace": [],
            "elapsed_ms": 275,
            "session_id": "mock-session",
        }


# ---------------------------------------------------------------------------
# Shared singletons (lazy-init)
# ---------------------------------------------------------------------------

_router: Optional[ModelRouter] = None
_guardrail: Optional[BedrockGuardrailChecker] = None
_agentcore: Optional[AgentCoreClient] = None


def get_model_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router


def get_guardrail_checker() -> BedrockGuardrailChecker:
    global _guardrail
    if _guardrail is None:
        _guardrail = BedrockGuardrailChecker()
    return _guardrail


def get_agentcore_client() -> AgentCoreClient:
    global _agentcore
    if _agentcore is None:
        _agentcore = AgentCoreClient()
    return _agentcore
