"""
Five enterprise pillars — runtime status for live demos (no secrets in response).

Maps to docs/ARCHITECTURE.md §2 (LLM Gateway, Evaluation, Governance) plus MCP + Observability.
"""

from __future__ import annotations

import os
import re
from typing import Any

from src.utils.jwt_verify import describe_auth_mode, jwt_auth_enabled
from src.utils.cloudwatch_emf import emf_enabled

# Keep this module import-light (do not import bedrock_client — pulls langchain_aws).

_INVOKE_AGENT_ID_RE = re.compile(r"^[0-9a-zA-Z]{1,10}$")


def _effective_guardrail_id() -> str:
    raw = os.getenv("BEDROCK_GUARDRAIL_ID", "").strip()
    if not raw:
        return ""
    rl = raw.lower()
    if rl.startswith("your-") or rl in frozenset({"changeme", "placeholder"}):
        return ""
    return raw


def _invoke_agent_ids_valid(agent_id: str, alias_id: str) -> bool:
    a = (agent_id or "").strip()
    b = (alias_id or "").strip()
    return bool(_INVOKE_AGENT_ID_RE.match(a) and _INVOKE_AGENT_ID_RE.match(b))


def _langsmith_effectively_on() -> bool:
    if os.getenv("LANGCHAIN_TRACING_V2", "").lower() in ("false", "0", "no", "off"):
        return False
    key = (os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY") or "").strip()
    if not key:
        return False
    bad = {x.lower() for x in ("your-langsmith-api-key", "changeme", "placeholder")}
    return key.lower() not in bad


def _running_on_lambda() -> bool:
    return bool(os.getenv("AWS_EXECUTION_ENV", "").strip())


def _agentcore_configured() -> bool:
    if os.getenv("USE_AGENTCORE", "true").lower() in ("0", "false", "no", "off"):
        return False
    aid = os.getenv("AGENTCORE_AGENT_ID", "").strip()
    alias = os.getenv("AGENTCORE_AGENT_ALIAS_ID", "").strip()
    return _invoke_agent_ids_valid(aid, alias)


def _xray_lambda_active() -> bool:
    return bool(os.getenv("_X_AMZN_TRACE_ID", "").strip())


def build_pillar_demo_report() -> dict[str, Any]:
    """
    Structured report for GET /demo/pillars — safe to expose (no API keys, no PHI).
    """
    gw = {
        "pillar": "llm_gateway",
        "title": "LLM Gateway (edge + Bedrock + routing + guardrails + AgentCore)",
        "implemented": True,
        "components": {
            "edge_api_gateway_lambda": {
                "active": _running_on_lambda(),
                "description": "HTTP API → Lambda → FastAPI (Mangum); local = uvicorn",
            },
            "amazon_bedrock_converse": {
                "active": True,
                "bedrock_region": os.getenv("BEDROCK_REGION", os.getenv("AWS_REGION", "us-east-1")),
                "description": "ChatBedrockConverse (Haiku / Sonnet) in src/utils/bedrock_client.py",
            },
            "model_router": {
                "active": True,
                "description": "ModelRouter selects fast vs powerful models per node",
            },
            "bedrock_guardrails": {
                "active": bool(_effective_guardrail_id()),
                "guardrail_configured": bool(_effective_guardrail_id()),
                "description": "ApplyGuardrail + optional Converse guardrail_config when BEDROCK_GUARDRAIL_ID set",
            },
            "agentcore_invoke_agent": {
                "active": _agentcore_configured(),
                "use_agentcore_env": os.getenv("USE_AGENTCORE", "true"),
                "description": "bedrock-agent-runtime InvokeAgent when AGENTCORE_AGENT_ID/ALIAS valid; else mock",
            },
        },
        "demo": {
            "try": [
                "GET /health — service + auth_mode",
                "GET /docs — OpenAPI",
                "POST /claims/adjudicate with {\"use_demo_claim\": true, \"actor_role\": \"claims_processor\"}",
            ],
        },
    }

    ev = {
        "pillar": "evaluation",
        "title": "Evaluation (LangSmith + EMF + local eval scripts)",
        "implemented": True,
        "components": {
            "langsmith_tracing": {
                "active": _langsmith_effectively_on(),
                "project": os.getenv("LANGCHAIN_PROJECT", os.getenv("LANGSMITH_PROJECT", "calclaim-demo")),
                "description": "LangGraph/LangChain spans when LANGCHAIN_API_KEY valid",
            },
            "cloudwatch_emf": {
                "active": emf_enabled(),
                "namespace": "CalcClaim/Workflow",
                "description": "emit_adjudication_emf() after adjudication; ENABLE_CLOUDWATCH_EMF",
            },
            "deterministic_evaluators": {
                "active": True,
                "description": "scripts/run_llm_eval_demo.py + langsmith_config evaluators (run locally)",
            },
            "bedrock_evaluation_jobs": {
                "active": False,
                "description": "Roadmap: Bedrock console evaluation jobs (see docs/ARCHITECTURE.md §2.2)",
            },
        },
        "demo": {
            "try": [
                "After adjudication: LangSmith project calclaim-demo",
                "CloudWatch → Metrics → CalcClaim/Workflow",
                "Local: python scripts/run_llm_eval_demo.py",
            ],
        },
    }

    gov = {
        "pillar": "governance",
        "title": "Governance (RBAC-style policy, PII scrub, OPA, HITL, audit, optional JWT)",
        "implemented": True,
        "components": {
            "rbac_policy_engine": {
                "active": True,
                "description": "policy_engine + phi_access_check in LangGraph; viewer cannot adjudicate",
            },
            "pii_scrub": {"active": True, "description": "pii_scrub node before any LLM"},
            "opa_http": {
                "active": os.getenv("USE_OPA", "").lower() in ("1", "true", "yes")
                and bool(os.getenv("OPA_SERVER_URL", "").strip()),
                "opa_server_configured": bool(os.getenv("OPA_SERVER_URL", "").strip()),
                "description": "USE_OPA + OPA_SERVER_URL → policies/calclaim.rego",
            },
            "hitl_sns": {
                "active": bool(os.getenv("HITL_SNS_TOPIC_ARN", "").strip()),
                "description": "hitl_gate + SNS topic ARN when deployed",
            },
            "immutable_audit": {
                "active": os.getenv("DEMO_MODE", "").lower() not in ("1", "true", "yes"),
                "demo_mode": os.getenv("DEMO_MODE", "false"),
                "description": "DynamoDB audit when DEMO_MODE=false",
            },
            "jwt_edge": {
                "active": jwt_auth_enabled()
                or os.getenv("TRUST_API_GATEWAY_AUTH", "").lower() in ("1", "true", "yes"),
                "auth_mode": describe_auth_mode(),
                "description": "REQUIRE_AUTH+JWT_JWKS_URL or API Gateway JWT (TRUST_API_GATEWAY_AUTH)",
            },
        },
        "demo": {
            "try": [
                "POST /demo/governance/policy-probe {\"actor_role\": \"viewer\", \"action\": \"adjudicate\"}",
                "POST /claims/adjudicate with actor_role viewer — governance deny",
                "POST /claims/reverse — HITL path",
                "GET /claims/{id}/audit — audit trail",
            ],
        },
    }

    try:
        from src.utils.mcp_workflow_client import mcp_package_available

        mcp_pkg = mcp_package_available()
    except Exception:
        mcp_pkg = False
    mcp_url = os.getenv("CALCLAIM_MCP_URL", "").strip()
    use_mcp_tools = os.getenv("USE_MCP_TOOLS", "true").lower() in ("1", "true", "yes")
    mcp_active = mcp_pkg and bool(mcp_url) and use_mcp_tools

    mcp = {
        "pillar": "mcp",
        "title": "MCP (tool plane — separate from REST adjudication)",
        "implemented": True,
        "components": {
            "mcp_tool_plane": {
                "active": mcp_active,
                "package_installed": mcp_pkg,
                "url_configured": bool(mcp_url),
                "use_mcp_tools": use_mcp_tools,
                "description": "mcp_tools node → formulary_tier_lookup (streamable HTTP)",
            },
        },
        "demo": {
            "try": [
                "Run mcp_servers/calclaim_mcp with MCP_TRANSPORT=streamable-http",
                "Set Lambda CALCLAIM_MCP_URL and USE_MCP_TOOLS=true",
                "POST /claims/adjudicate — workflow_steps should include mcp_tools (not mcp_tools_skipped)",
            ],
        },
    }

    obs = {
        "pillar": "observability",
        "title": "Observability (LangSmith + CloudWatch + correlation ID + X-Ray)",
        "implemented": True,
        "components": {
            "langsmith": {
                "active": _langsmith_effectively_on(),
                "description": "Same as Evaluation — trace export",
            },
            "cloudwatch_logs": {
                "active": _running_on_lambda(),
                "log_group_hint": "/aws/lambda/calclaim-api",
                "description": "Lambda stdout; LOG_FORMAT=json adds correlation_id",
            },
            "correlation_id": {
                "active": True,
                "description": "X-Correlation-ID / X-Request-ID middleware",
            },
            "x_ray": {
                "active": _xray_lambda_active(),
                "description": "Lambda active tracing → _X_AMZN_TRACE_ID present when enabled",
            },
            "embedded_metrics_emf": {
                "active": emf_enabled(),
                "description": "CalcClaim/Workflow metrics from adjudication",
            },
        },
        "demo": {
            "try": [
                "curl -H 'X-Correlation-ID: demo-1' ... then filter logs",
                "X-Ray console service map",
                "LangSmith traces for same correlation metadata",
            ],
        },
    }

    return {
        "schema_version": "1.0",
        "summary": {
            "llm_gateway": True,
            "evaluation": _langsmith_effectively_on() or emf_enabled(),
            "governance": True,
            "mcp": mcp_active,
            "observability": True,
        },
        "pillars": {
            "llm_gateway": gw,
            "evaluation": ev,
            "governance": gov,
            "mcp": mcp,
            "observability": obs,
        },
    }
