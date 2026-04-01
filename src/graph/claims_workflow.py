"""
CalcClaim LangGraph Workflow — Enterprise Agentic AI demo.

Deterministic **calcClaim2** pipeline nodes run on the claims path (after MCP):
CostCalculationCore → CopayCalculator → MedicareDProcessor → MarginProcessor →
DeductibleCapProcessor → SpecialProcessor → ClaimCalculationOrchestrator
(``validateResults``). Results live in ``calc_claim2_context``; the LLM aligns
with that output. AgentCore/MCP still supply optional tool context.

Graph topology:
  START
    │
    ▼
  pii_scrub_node        (PHIScrubber — strip PII before any LLM sees it)
    │
    ▼
  phi_access_check      (HIPAA minimum-necessary policy)
    │
    ├─► [DENY]  ──► governance_deny_node ──► END
    │
    ▼
  supervisor_node       (Claude Haiku — intent classification + routing)
    │
    ├─► agentcore_calcclaim   (Bedrock AgentCore — CalcClaim server / invoke_agent)
    │         │
    │         ▼
    │   mcp_tools_node        (optional — MCP streamable HTTP formulary_tier_lookup)
    │         │
    │         ▼
    │   calc_claim2_*         (7 nodes — CostCore, Copay, MedicareD, Margin, DedCap, Special, Orchestrator)
    │         │
    │         ▼
    │   claims_agent_node     (Claude Sonnet — adjudication; calcClaim2 JSON + AgentCore + MCP)
    ├─► formulary_node        (Claude Haiku — formulary lookup)
    └─► compliance_node       (Claude Sonnet — HIPAA/policy audit)
              │
              ▼
          policy_gate_node    (OPA policy evaluation)
              │
              ├─► [REQUIRE_HITL] ──► hitl_node ──► wait_for_hitl
              │
              ▼
          guardrail_check_node  (Bedrock Guardrails on output)
              │
              ▼
          audit_node            (Immutable audit record)
              │
              ▼
          response_node
              │
              ▼
            END
"""

from __future__ import annotations

import os
import json
import logging
import uuid
from typing import Any, Optional

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, START, END

from src.graph.state import ClaimWorkflowState
from src.governance import (
    get_audit_logger,
    get_scrubber,
    get_policy_engine,
    get_hitl_gate,
)
from src.utils.bedrock_client import (
    get_model_router,
    get_guardrail_checker,
    get_agentcore_client,
)
from src.utils.langsmith_config import build_run_metadata
from src.utils.launchdarkly_flags import use_agentcore_effective, use_mcp_tools_effective
from src.graph.calc_claim2_components import (
    ClaimCalculationOrchestrator,
    CopayCalculator,
    CostCalculationCore,
    DeductibleCapProcessor,
    MarginProcessor,
    MedicareDProcessor,
    SpecialProcessor,
)

logger = logging.getLogger(__name__)


def _cc2_ctx_from_state(state: ClaimWorkflowState) -> dict[str, Any]:
    existing = state.get("calc_claim2_context")
    if not existing:
        return {"return_code": 0, "error_message": None}
    return dict(existing)


def _cc2_copay_after_medicare(ctx: dict[str, Any]) -> float:
    cop = ctx.get("copay") or {}
    amt = float(cop.get("copay_amount") or 0)
    md = ctx.get("medicare_d") or {}
    if md.get("applied"):
        amt = float(md.get("adjusted_copay") or amt)
    return amt


def _synthetic_adjudication_from_calc_claim2(
    state: ClaimWorkflowState,
    llm_error: Optional[str] = None,
) -> dict[str, Any]:
    """
    Build adjudication JSON from deterministic calcClaim2 context when Bedrock LLM
    is unavailable or returns invalid model / parse errors — keeps demos usable.
    """
    safe = state.get("safe_claim") or {}
    cc2 = state.get("calc_claim2_context") or {}
    orch = cc2.get("orchestrator") or {}

    raw_status = (safe.get("status") or "approved").lower()
    if "rejected" in raw_status and "pa" in raw_status:
        status = "pending_pa"
    elif "rejected" in raw_status or raw_status == "reversed":
        status = "rejected"
    elif "approved" in raw_status:
        status = "approved"
    else:
        status = "pending_review"

    patient = float(orch.get("patient_pay_demo") or 0)
    plan = float(orch.get("plan_pay_demo") or 0)
    if patient == 0 and plan == 0 and cc2:
        cop = cc2.get("copay") or {}
        md = cc2.get("medicare_d") or {}
        c = float(md.get("adjusted_copay") if md.get("applied") else cop.get("copay_amount") or 0)
        margin = cc2.get("margin") or {}
        ded = cc2.get("deductible_cap") or {}
        plan = float(ded.get("plan_pay_after_caps") or margin.get("plan_pay_after_margin") or 0)
        spec = cc2.get("special") or {}
        patient = c + float(spec.get("extra_patient_amount") or 0)

    reject_code = safe.get("reject_code")
    reject_reason = safe.get("reject_message") or safe.get("reject_reason")
    if status == "approved":
        reject_code = None
        reject_reason = None

    reasoning_parts = [
        "Deterministic adjudication from calcClaim2 pipeline (CostCore → Copay → Medicare D → "
        "Margin → Deductible/caps → Special → validateResults)."
    ]
    if llm_error:
        reasoning_parts.append(f"LLM step skipped or failed ({llm_error[:200]}); pricing taken from pipeline.")

    return {
        "status": status,
        "reject_code": reject_code if status in ("rejected", "pending_pa") else None,
        "reject_reason": reject_reason if status in ("rejected", "pending_pa") else None,
        "copay": round(patient, 2),
        "plan_pay": round(plan, 2),
        "dur_alerts": list(safe.get("dur_alerts") or []),
        "reasoning": " ".join(reasoning_parts),
        "confidence": 0.88 if orch.get("validated") else 0.55,
        "synthetic_from_calc_claim2": True,
    }


def _use_synthetic_claims_agent_only() -> bool:
    return os.getenv("CALCLAIM_SYNTHETIC_CLAIMS_AGENT", "").lower() in ("1", "true", "yes")


def _synthetic_fallback_on_llm_error() -> bool:
    return os.getenv("CALCLAIM_SYNTHETIC_FALLBACK_ON_LLM_ERROR", "true").lower() in (
        "1",
        "true",
        "yes",
    )

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SUPERVISOR_SYSTEM = """You are the Navitus CalcClaim Supervisor Agent.

Your job is to classify the intent of the incoming pharmacy benefit claim request
and route it to the correct specialist agent.

Available agents:
- claims_agent: Claim adjudication, copay calculation, DUR checks, NCRX workflow
- formulary_agent: Drug formulary lookup, prior authorization eligibility, tier coverage
- compliance_agent: HIPAA compliance review, PHI audit, policy violation investigation

Respond in JSON with this exact schema:
{
  "intent": "<one of: adjudicate|formulary_lookup|pa_evaluation|compliance_review|claim_status>",
  "routed_agent": "<one of: claims_agent|formulary_agent|compliance_agent>",
  "reasoning": "<1-2 sentences>"
}"""

CLAIMS_AGENT_SYSTEM = """You are the Navitus CalcClaim Adjudication Agent.

The graph already executed a deterministic **calcClaim2**-style pipeline
(CostCalculationCore → CopayCalculator → MedicareDProcessor → MarginProcessor →
DeductibleCapProcessor → SpecialProcessor → orchestrator ``validateResults``).
You receive its JSON as ground truth for pricing math unless it conflicts with
eligibility, formulary, DUR, PA, or AgentCore/MCP — then explain in ``reasoning``.

Given a claim, determine:
1. Is the member eligible on the date of service?
2. Is the drug covered under the formulary (tier + plan)?
3. Are there any DUR alerts (drug interactions, duplicate therapy)?
4. Is prior authorization required and on file?
5. Emit ``copay`` and ``plan_pay`` consistent with the pipeline's patient/plan demo
   fields when possible; set ``status`` from clinical rules.

Respond in JSON:
{
  "status": "<approved|rejected|pending_pa|pending_review>",
  "reject_code": "<NCPDP reject code or null>",
  "reject_reason": "<human-readable or null>",
  "copay": <float>,
  "plan_pay": <float>,
  "dur_alerts": [...],
  "reasoning": "<clinical/formulary reasoning>",
  "confidence": <0.0-1.0>
}

Do NOT include member PII (SSN, DOB, email, phone) in your response."""

FORMULARY_SYSTEM = """You are the Navitus Formulary Agent.

Evaluate drug formulary coverage, tier placement, and prior authorization requirements.
Always reference the plan formulary and applicable PA criteria.

Respond in JSON:
{
  "covered": <bool>,
  "tier": <1-5>,
  "pa_required": <bool>,
  "pa_criteria_met": <bool|null>,
  "alternatives": [...],
  "reasoning": "<formulary reasoning>"
}"""

COMPLIANCE_SYSTEM = """You are the Navitus Compliance Agent.

Review claims for HIPAA compliance, PHI handling obligations, and regulatory policy adherence.
Flag any violations, minimum-necessary access concerns, or audit requirements.

Respond in JSON:
{
  "compliant": <bool>,
  "violations": [...],
  "recommendations": [...],
  "hipaa_flags": [...],
  "audit_required": <bool>
}"""


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------

def pii_scrub_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """Strip PII/PHI from claim before passing to any LLM."""
    scrubber = get_scrubber()
    audit = get_audit_logger()
    claim = state.get("raw_claim", {})
    claim_id = state.get("claim_id", claim.get("claim_id", "UNKNOWN"))

    safe_claim = dict(claim)
    member = claim.get("member", {})
    safe_claim["member"] = scrubber.mask_member_pii(member)

    safe_claim_str = json.dumps(safe_claim)
    scrubbed_str, entities = scrubber.scrub_text(safe_claim_str)

    event_id = audit.log(
        "PII_SCRUB",
        claim_id=claim_id,
        actor=state.get("actor_id", "system"),
        details={"entities_found": entities, "member_id": member.get("member_id")},
        session_id=state.get("session_id", ""),
    )

    steps = list(state.get("workflow_steps", []))
    steps.append("pii_scrub")

    return {
        "safe_claim": json.loads(scrubbed_str),
        "pii_entities_found": entities,
        "current_step": "pii_scrub",
        "workflow_steps": steps,
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [event_id],
    }


def _phi_purpose_for_action(action: str) -> str:
    """Map workflow action → HIPAA purpose string checked by the policy engine."""
    mapping = {
        "adjudicate": "claim_processing",
        "reverse": "claim_processing",
        "approve": "claim_processing",
        "query": "operations",
        "read": "operations",
    }
    return mapping.get((action or "adjudicate").lower(), "claim_processing")


def phi_access_check_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """HIPAA minimum-necessary PHI access policy check."""
    policy = get_policy_engine()
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    member_id = state.get("safe_claim", {}).get("member", {}).get("member_id", "")

    result = policy.evaluate_phi_access(
        actor_id=state.get("actor_id", "system"),
        purpose=_phi_purpose_for_action(state.get("action", "adjudicate")),
        member_id=member_id,
    )

    event_id = audit.log(
        "GOVERNANCE_CHECK",
        claim_id=claim_id,
        actor=state.get("actor_id", "system"),
        details={"policy_id": result.policy_id, "decision": result.decision, "reason": result.reason},
        session_id=state.get("session_id", ""),
        outcome=result.decision,
    )
    steps = list(state.get("workflow_steps", []))
    steps.append("phi_access_check")

    return {
        "policy_result": result.__dict__,
        "current_step": "phi_access_check",
        "workflow_steps": steps,
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [event_id],
    }


def supervisor_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """Classify intent and route to specialist agent."""
    router = get_model_router()
    llm = router.route("supervisor_routing")
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")

    safe_claim = state.get("safe_claim", {})
    claim_summary = {
        "drug": safe_claim.get("drug", {}).get("name"),
        "tier": safe_claim.get("drug", {}).get("tier"),
        "status": safe_claim.get("status"),
        "requires_pa": safe_claim.get("prior_auth", {}).get("required"),
        "reject_code": safe_claim.get("reject_code"),
        "action_requested": state.get("action", "adjudicate"),
    }

    messages = [
        SystemMessage(content=SUPERVISOR_SYSTEM),
        HumanMessage(content=f"Route this claim request:\n{json.dumps(claim_summary, indent=2)}"),
    ]

    try:
        response = llm.invoke(messages)
        content = response.content
        parsed = json.loads(content)
        intent = parsed.get("intent", "adjudicate")
        routed_agent = parsed.get("routed_agent", "claims_agent")
        reasoning = parsed.get("reasoning", "")
    except Exception as exc:
        logger.warning("Supervisor parse error: %s — defaulting to claims_agent", exc)
        intent = "adjudicate"
        routed_agent = "claims_agent"
        reasoning = "Parse error — default routing"
        content = f'{{"intent":"{intent}","routed_agent":"{routed_agent}","reasoning":"{reasoning}"}}'

    event_id = audit.log(
        "ADJUDICATION_STARTED",
        claim_id=claim_id,
        actor="supervisor_agent",
        details={"intent": intent, "routed_to": routed_agent, "reasoning": reasoning},
        session_id=state.get("session_id", ""),
    )
    steps = list(state.get("workflow_steps", []))
    steps.append("supervisor")

    return {
        "intent": intent,
        "routed_agent": routed_agent,
        "messages": [AIMessage(content=content, name="supervisor")],
        "current_step": "supervisor",
        "workflow_steps": steps,
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [event_id],
    }


def agentcore_calcclaim_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """
    Amazon Bedrock AgentCore — CalcClaim adjudication tool server.

    Calls bedrock-agent-runtime ``invoke_agent`` when ``AGENTCORE_AGENT_ID`` is set;
    otherwise returns a mock completion (demo). Feeds the claims LLM as server context.
    """
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    session_id = state.get("session_id", str(uuid.uuid4()))

    if not use_agentcore_effective(state):
        steps = list(state.get("workflow_steps", [])) + ["agentcore_calcclaim_skipped"]
        return {
            "agentcore_result": {"completion": "", "skipped": True, "elapsed_ms": 0},
            "workflow_steps": steps,
            "current_step": "agentcore_skipped",
        }

    safe = state.get("safe_claim", {})
    payload = {
        "claim_id": claim_id,
        "action": state.get("action", "adjudicate"),
        "drug": safe.get("drug"),
        "member_plan_id": safe.get("member", {}).get("plan", {}).get("plan_id"),
        "prior_auth": safe.get("prior_auth"),
        "pricing": safe.get("pricing"),
    }
    input_text = (
        "CalcClaim adjudication request (PHI-safe summary). "
        "Return structured adjudication hints.\n\n"
        f"{json.dumps(payload, indent=2)}"
    )

    client = get_agentcore_client()
    result = client.invoke(session_id, input_text)

    event_id = audit.log(
        "AGENTCORE_INVOKED",
        claim_id=claim_id,
        actor="bedrock_agentcore",
        details={
            "elapsed_ms": result.get("elapsed_ms"),
            "trace_steps": len(result.get("trace") or []),
            "completion_preview": (result.get("completion") or "")[:200],
        },
        session_id=session_id,
        outcome="SUCCESS",
    )
    steps = list(state.get("workflow_steps", []))
    steps.append("agentcore_calcclaim")

    return {
        "agentcore_result": result,
        "workflow_steps": steps,
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [event_id],
        "current_step": "agentcore_calcclaim",
    }


def mcp_tools_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """
    Optional MCP (streamable HTTP) — call ``formulary_tier_lookup`` on the CalcClaim MCP server.

    Configure ``CALCLAIM_MCP_URL`` (e.g. ``http://127.0.0.1:8765/mcp``), install ``mcp``, run
    ``mcp_servers`` in streamable-http mode. Set ``USE_MCP_TOOLS=false`` to skip.
    """
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    session_id = state.get("session_id", "")
    steps = list(state.get("workflow_steps", []))

    if not use_mcp_tools_effective(state):
        steps.append("mcp_tools_skipped")
        return {
            "mcp_tool_results": {},
            "workflow_steps": steps,
            "current_step": "mcp_tools_skipped",
        }

    url = os.getenv("CALCLAIM_MCP_URL", "").strip()
    if not url:
        steps.append("mcp_tools_skipped")
        return {
            "mcp_tool_results": {},
            "workflow_steps": steps,
            "current_step": "mcp_tools_skipped",
        }

    from src.utils.mcp_workflow_client import (
        MCPURLError,
        mcp_package_available,
        run_mcp_formulary_sync,
        validate_mcp_url,
    )

    if not mcp_package_available():
        logger.warning(
            "CALCLAIM_MCP_URL is set but the `mcp` package is not installed — skipping MCP tools"
        )
        steps.append("mcp_tools_skipped")
        return {
            "mcp_tool_results": {},
            "workflow_steps": steps,
            "current_step": "mcp_tools_skipped",
        }

    safe = state.get("safe_claim", {})
    drug = safe.get("drug") or {}
    ndc = (drug.get("ndc") or "").strip()
    plan = (safe.get("member", {}).get("plan", {}).get("plan_id") or "commercial_ppo")

    if not ndc:
        steps.append("mcp_tools_skipped")
        return {
            "mcp_tool_results": {},
            "workflow_steps": steps,
            "current_step": "mcp_tools_skipped",
        }

    try:
        validate_mcp_url(url)
    except MCPURLError as exc:
        logger.warning("MCP URL rejected: %s", exc)
        steps.append("mcp_tools_skipped")
        return {
            "mcp_tool_results": {"error": str(exc)},
            "workflow_steps": steps,
            "current_step": "mcp_tools_skipped",
        }

    hint = run_mcp_formulary_sync(url, ndc, plan)
    if hint is None:
        steps.append("mcp_tools_skipped")
        return {
            "mcp_tool_results": {},
            "workflow_steps": steps,
            "current_step": "mcp_tools_skipped",
        }

    steps.append("mcp_tools")
    event_id = audit.log(
        "MCP_TOOLS_INVOKED",
        claim_id=claim_id,
        actor="mcp_bridge",
        details={
            "tool": "formulary_tier_lookup",
            "ndc": ndc,
            "plan_id": plan,
            "hint_ok": hint.get("ok", True) if isinstance(hint, dict) else False,
        },
        session_id=session_id,
        outcome="SUCCESS" if (isinstance(hint, dict) and hint.get("ok") is not False) else "PARTIAL",
    )

    return {
        "mcp_tool_results": {"formulary_tier_lookup": hint},
        "workflow_steps": steps,
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [event_id],
        "current_step": "mcp_tools",
    }


# ---------------------------------------------------------------------------
# calcClaim2 pipeline nodes (deterministic demo — see calc_claim2_components.py)
# ---------------------------------------------------------------------------


def calc_claim2_cost_core_node(state: ClaimWorkflowState) -> dict[str, Any]:
    mtx = state.get("safe_claim") or {}
    ctx = _cc2_ctx_from_state(state)
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    try:
        ctx["cost"] = CostCalculationCore().calculate_basic_costs(mtx)
        if ctx.get("return_code") != 6:
            ctx["return_code"] = 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("calc_claim2 cost core: %s", exc)
        ctx["return_code"] = 6
        ctx["error_message"] = str(exc)
    steps = list(state.get("workflow_steps", [])) + ["calc_claim2_cost_core"]
    eid = audit.log(
        "CALC_CLAIM2_STAGE",
        claim_id=claim_id,
        actor="CostCalculationCore",
        details={"stage": "calculateBasicCosts", "return_code": ctx.get("return_code")},
        session_id=state.get("session_id", ""),
        outcome="ERROR" if ctx.get("return_code") == 6 else "SUCCESS",
    )
    return {
        "calc_claim2_context": ctx,
        "workflow_steps": steps,
        "current_step": "calc_claim2_cost_core",
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [eid],
    }


def calc_claim2_copay_node(state: ClaimWorkflowState) -> dict[str, Any]:
    mtx = state.get("safe_claim") or {}
    ctx = _cc2_ctx_from_state(state)
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    cost = ctx.get("cost")
    if not cost:
        ctx["return_code"] = 6
        ctx.setdefault("error_message", "missing cost stage")
    else:
        try:
            ctx["copay"] = CopayCalculator().calculate_copay(mtx, cost)
        except Exception as exc:  # noqa: BLE001
            logger.warning("calc_claim2 copay: %s", exc)
            ctx["return_code"] = 6
            ctx["error_message"] = str(exc)
    steps = list(state.get("workflow_steps", [])) + ["calc_claim2_copay"]
    eid = audit.log(
        "CALC_CLAIM2_STAGE",
        claim_id=claim_id,
        actor="CopayCalculator",
        details={"stage": "calculateCopay"},
        session_id=state.get("session_id", ""),
        outcome="ERROR" if ctx.get("return_code") == 6 else "SUCCESS",
    )
    return {
        "calc_claim2_context": ctx,
        "workflow_steps": steps,
        "current_step": "calc_claim2_copay",
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [eid],
    }


def calc_claim2_medicare_d_node(state: ClaimWorkflowState) -> dict[str, Any]:
    mtx = state.get("safe_claim") or {}
    ctx = _cc2_ctx_from_state(state)
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    cop = ctx.get("copay") or {}
    try:
        ctx["medicare_d"] = MedicareDProcessor().process_medicare_d(mtx, cop)
    except Exception as exc:  # noqa: BLE001
        logger.warning("calc_claim2 medicare_d: %s", exc)
        ctx["return_code"] = 6
        ctx["error_message"] = str(exc)
    steps = list(state.get("workflow_steps", [])) + ["calc_claim2_medicare_d"]
    eid = audit.log(
        "CALC_CLAIM2_STAGE",
        claim_id=claim_id,
        actor="MedicareDProcessor",
        details={"stage": "processMedicareD", "part_d": (ctx.get("medicare_d") or {}).get("is_part_d")},
        session_id=state.get("session_id", ""),
        outcome="ERROR" if ctx.get("return_code") == 6 else "SUCCESS",
    )
    return {
        "calc_claim2_context": ctx,
        "workflow_steps": steps,
        "current_step": "calc_claim2_medicare_d",
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [eid],
    }


def calc_claim2_margin_node(state: ClaimWorkflowState) -> dict[str, Any]:
    mtx = state.get("safe_claim") or {}
    ctx = _cc2_ctx_from_state(state)
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    cost = ctx.get("cost")
    if not cost:
        ctx["return_code"] = 6
    else:
        try:
            copay_after = _cc2_copay_after_medicare(ctx)
            ctx["margin"] = MarginProcessor().process_margin(mtx, cost, copay_after)
        except Exception as exc:  # noqa: BLE001
            logger.warning("calc_claim2 margin: %s", exc)
            ctx["return_code"] = 6
            ctx["error_message"] = str(exc)
    steps = list(state.get("workflow_steps", [])) + ["calc_claim2_margin"]
    eid = audit.log(
        "CALC_CLAIM2_STAGE",
        claim_id=claim_id,
        actor="MarginProcessor",
        details={"stage": "processMargin"},
        session_id=state.get("session_id", ""),
        outcome="ERROR" if ctx.get("return_code") == 6 else "SUCCESS",
    )
    return {
        "calc_claim2_context": ctx,
        "workflow_steps": steps,
        "current_step": "calc_claim2_margin",
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [eid],
    }


def calc_claim2_deductible_cap_node(state: ClaimWorkflowState) -> dict[str, Any]:
    mtx = state.get("safe_claim") or {}
    ctx = _cc2_ctx_from_state(state)
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    margin = ctx.get("margin") or {}
    try:
        ctx["deductible_cap"] = DeductibleCapProcessor().process_deductible_and_caps(
            mtx, _cc2_copay_after_medicare(ctx), margin
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("calc_claim2 deductible_cap: %s", exc)
        ctx["return_code"] = 6
        ctx["error_message"] = str(exc)
    steps = list(state.get("workflow_steps", [])) + ["calc_claim2_deductible_cap"]
    eid = audit.log(
        "CALC_CLAIM2_STAGE",
        claim_id=claim_id,
        actor="DeductibleCapProcessor",
        details={"stage": "processDeductibleAndCaps"},
        session_id=state.get("session_id", ""),
        outcome="ERROR" if ctx.get("return_code") == 6 else "SUCCESS",
    )
    return {
        "calc_claim2_context": ctx,
        "workflow_steps": steps,
        "current_step": "calc_claim2_deductible_cap",
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [eid],
    }


def calc_claim2_special_node(state: ClaimWorkflowState) -> dict[str, Any]:
    mtx = state.get("safe_claim") or {}
    ctx = _cc2_ctx_from_state(state)
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    cost = ctx.get("cost") or {}
    try:
        ctx["special"] = SpecialProcessor().process_special_cases(
            mtx,
            {
                "total_cost_basis": cost.get("total_cost_basis"),
                "copay": _cc2_copay_after_medicare(ctx),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("calc_claim2 special: %s", exc)
        ctx["return_code"] = 6
        ctx["error_message"] = str(exc)
    steps = list(state.get("workflow_steps", [])) + ["calc_claim2_special"]
    eid = audit.log(
        "CALC_CLAIM2_STAGE",
        claim_id=claim_id,
        actor="SpecialProcessor",
        details={"stage": "processSpecialCases"},
        session_id=state.get("session_id", ""),
        outcome="ERROR" if ctx.get("return_code") == 6 else "SUCCESS",
    )
    return {
        "calc_claim2_context": ctx,
        "workflow_steps": steps,
        "current_step": "calc_claim2_special",
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [eid],
    }


def calc_claim2_orchestrator_node(state: ClaimWorkflowState) -> dict[str, Any]:
    mtx = state.get("safe_claim") or {}
    ctx = _cc2_ctx_from_state(state)
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    try:
        orch = ClaimCalculationOrchestrator()
        ctx["orchestrator"] = orch.validate_results(ctx, mtx)
        ctx["return_code"] = int((ctx["orchestrator"] or {}).get("return_code", 0))
    except Exception as exc:  # noqa: BLE001
        logger.warning("calc_claim2 orchestrator: %s", exc)
        ctx["return_code"] = 6
        ctx["error_message"] = str(exc)
    steps = list(state.get("workflow_steps", [])) + ["calc_claim2_orchestrator"]
    eid = audit.log(
        "CALC_CLAIM2_STAGE",
        claim_id=claim_id,
        actor="ClaimCalculationOrchestrator",
        details={
            "stage": "validateResults",
            "return_code": ctx.get("return_code"),
            "validated": (ctx.get("orchestrator") or {}).get("validated"),
        },
        session_id=state.get("session_id", ""),
        outcome="ERROR" if ctx.get("return_code") == 6 else "SUCCESS",
    )
    return {
        "calc_claim2_context": ctx,
        "workflow_steps": steps,
        "current_step": "calc_claim2_orchestrator",
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [eid],
    }


def claims_agent_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """Core adjudication agent (CalcClaim / NCRX workflow)."""
    router = get_model_router()
    llm = router.route("adjudication")
    claim_id = state.get("claim_id", "UNKNOWN")

    safe_claim = state.get("safe_claim", {})
    ac = state.get("agentcore_result") or {}
    ac_block = ""
    if ac.get("skipped"):
        ac_block = "\n\n(AgentCore step skipped: USE_AGENTCORE=false.)\n"
    elif ac.get("completion"):
        ac_block = (
            "\n\n--- Amazon Bedrock AgentCore (CalcClaim server) output ---\n"
            f"{ac['completion']}\n"
            "--- End AgentCore ---\n"
            "Treat the above as tool-server context; align your JSON adjudication with it "
            "when consistent with formulary and PA rules.\n"
        )

    mcp_block = ""
    mcp_res = state.get("mcp_tool_results") or {}
    if mcp_res.get("formulary_tier_lookup"):
        mcp_block = (
            "\n\n--- MCP tool: formulary_tier_lookup (Model Context Protocol) ---\n"
            f"{json.dumps(mcp_res['formulary_tier_lookup'], indent=2)}\n"
            "--- End MCP ---\n"
            "Use as supplemental demo formulary signal; final decision must still follow "
            "claim JSON and AgentCore context.\n"
        )

    cc2 = state.get("calc_claim2_context") or {}
    cc2_block = (
        "\n\n--- calcClaim2 pipeline (deterministic demo — CostCore, Copay, MedicareD, "
        "Margin, DeductibleCap, Special, Orchestrator validateResults) ---\n"
        f"{json.dumps(cc2, indent=2, default=str)}\n"
        "--- End calcClaim2 ---\n"
        "Use ``orchestrator.patient_pay_demo`` / ``orchestrator.plan_pay_demo`` for copay "
        "and plan_pay when consistent with clinical status.\n"
    )

    messages = [
        SystemMessage(content=CLAIMS_AGENT_SYSTEM),
        HumanMessage(content=(
            f"Adjudicate this claim:\n"
            f"```json\n{json.dumps(safe_claim, indent=2)}\n```\n\n"
            f"Action requested: {state.get('action', 'adjudicate')}"
            f"{ac_block}{mcp_block}{cc2_block}"
        )),
    ]

    result: dict[str, Any]
    content: str

    if _use_synthetic_claims_agent_only():
        result = _synthetic_adjudication_from_calc_claim2(state, llm_error="CALCLAIM_SYNTHETIC_CLAIMS_AGENT=true")
        content = json.dumps(result)
        logger.info("Claims agent: synthetic-only mode (no Bedrock invoke)")
    else:
        try:
            response = llm.invoke(messages)
            content = response.content
            result = json.loads(content)
        except Exception as exc:
            err = str(exc)
            logger.warning("Claims agent parse error: %s", exc)
            if _synthetic_fallback_on_llm_error() and state.get("calc_claim2_context"):
                result = _synthetic_adjudication_from_calc_claim2(state, llm_error=err)
                content = json.dumps(result)
                logger.info("Claims agent: using calcClaim2 synthetic fallback after LLM error")
            else:
                result = {
                    "status": "pending_review",
                    "reject_code": None,
                    "reject_reason": f"Agent parse error: {exc}",
                    "copay": 0.0,
                    "plan_pay": 0.0,
                    "dur_alerts": [],
                    "reasoning": "Parse error — flagged for review",
                    "confidence": 0.0,
                }
                content = json.dumps(result)

    steps = list(state.get("workflow_steps", []))
    steps.append("claims_agent")

    return {
        "adjudication_result": result,
        "adjudication_reasoning": result.get("reasoning", ""),
        "messages": [AIMessage(content=content, name="claims_agent")],
        "current_step": "claims_agent",
        "workflow_steps": steps,
    }


def formulary_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """Formulary coverage and PA eligibility check."""
    router = get_model_router()
    llm = router.route("formulary_lookup")
    safe_claim = state.get("safe_claim", {})

    messages = [
        SystemMessage(content=FORMULARY_SYSTEM),
        HumanMessage(content=(
            f"Evaluate formulary coverage:\n"
            f"Drug: {safe_claim.get('drug', {}).get('name')}\n"
            f"Tier: {safe_claim.get('drug', {}).get('tier')}\n"
            f"Plan: {safe_claim.get('member', {}).get('plan_id')}\n"
            f"PA status: {json.dumps(safe_claim.get('prior_auth', {}))}"
        )),
    ]

    try:
        response = llm.invoke(messages)
        content = response.content
        result = json.loads(content)
    except Exception as exc:
        result = {"covered": False, "pa_required": True, "reasoning": str(exc)}
        content = json.dumps(result)

    steps = list(state.get("workflow_steps", []))
    steps.append("formulary_agent")

    return {
        "adjudication_result": result,
        "messages": [AIMessage(content=content, name="formulary_agent")],
        "current_step": "formulary_agent",
        "workflow_steps": steps,
    }


def compliance_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """HIPAA compliance and PHI audit review."""
    router = get_model_router()
    llm = router.route("compliance_review")
    safe_claim = state.get("safe_claim", {})

    messages = [
        SystemMessage(content=COMPLIANCE_SYSTEM),
        HumanMessage(content=(
            f"Review claim for HIPAA compliance:\n"
            f"Claim ID: {state.get('claim_id')}\n"
            f"PII entities found in raw input: {state.get('pii_entities_found', [])}\n"
            f"Actor: {state.get('actor_id')} (role: {state.get('actor_role')})\n"
            f"Drug tier: {safe_claim.get('drug', {}).get('tier')}\n"
            f"Action: {state.get('action')}"
        )),
    ]

    try:
        response = llm.invoke(messages)
        content = response.content
        result = json.loads(content)
    except Exception as exc:
        result = {"compliant": True, "violations": [], "audit_required": False}
        content = json.dumps(result)

    steps = list(state.get("workflow_steps", []))
    steps.append("compliance_agent")

    return {
        "adjudication_result": result,
        "messages": [AIMessage(content=content, name="compliance_agent")],
        "current_step": "compliance_agent",
        "workflow_steps": steps,
    }


def policy_gate_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """OPA policy evaluation on the adjudication result."""
    policy = get_policy_engine()
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    safe_claim = state.get("safe_claim", {})

    # Check claim access policy
    access_result = policy.evaluate_claim_access(
        actor_role=state.get("actor_role", "claims_processor"),
        claim=safe_claim,
        action=state.get("action", "adjudicate"),
    )

    # Check formulary coverage policy
    drug_tier = safe_claim.get("drug", {}).get("tier", 2)
    plan_id = safe_claim.get("member", {}).get("plan", {}).get("plan_id", "")
    form_result = policy.evaluate_formulary_coverage(drug_tier, plan_id)

    # Use the more restrictive of the two
    final = access_result if access_result.decision != "ALLOW" else form_result

    event_id = audit.log(
        "POLICY_EVALUATED",
        claim_id=claim_id,
        actor="policy_engine",
        details={
            "access_policy": access_result.__dict__,
            "formulary_policy": form_result.__dict__,
            "final_decision": final.decision,
        },
        session_id=state.get("session_id", ""),
        outcome=final.decision,
    )
    steps = list(state.get("workflow_steps", []))
    steps.append("policy_gate")

    return {
        "policy_result": final.__dict__,
        "current_step": "policy_gate",
        "workflow_steps": steps,
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [event_id],
    }


def hitl_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """Trigger HITL review and block until resolved."""
    hitl = get_hitl_gate()
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    policy_result = state.get("policy_result", {})

    trigger_map = {
        "REQUIRE_HITL": "HIGH_VALUE_CLAIM",
        "REQUIRE_DUAL_APPROVAL": "DESTRUCTIVE_ACTION",
        "DENY": "POLICY_DENY",
    }
    trigger_type = trigger_map.get(policy_result.get("decision", "DENY"), "POLICY_DENY")

    req = hitl.trigger(
        trigger_type=trigger_type,
        claim_id=claim_id,
        reason=policy_result.get("reason", "Policy triggered HITL"),
        context={"policy_result": policy_result, "safe_claim": state.get("safe_claim", {})},
        session_id=state.get("session_id", ""),
    )

    event_id = audit.log(
        "HITL_TRIGGERED",
        claim_id=claim_id,
        actor="policy_gate",
        details={"request_id": req.request_id, "trigger_type": trigger_type,
                 "resolution": req.resolution},
        session_id=state.get("session_id", ""),
    )
    steps = list(state.get("workflow_steps", []))
    steps.append("hitl_gate")

    return {
        "hitl_request_id": req.request_id,
        "hitl_resolution": req.resolution,
        "current_step": "hitl_gate",
        "workflow_steps": steps,
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [event_id],
    }


def governance_deny_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """Hard deny — write final audit and build error response."""
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")

    event_id = audit.log(
        "ADJUDICATION_COMPLETE",
        claim_id=claim_id,
        actor="governance_gate",
        details={"outcome": "DENIED", "policy": state.get("policy_result", {})},
        session_id=state.get("session_id", ""),
        outcome="DENIED",
    )
    steps = list(state.get("workflow_steps", [])) + ["governance_deny"]
    return {
        "final_response": {
            "claim_id": claim_id,
            "status": "denied",
            "reason": state.get("policy_result", {}).get("reason", "Policy denied"),
            "policy_id": state.get("policy_result", {}).get("policy_id"),
            "workflow_steps": steps,
            "audit_trail": list(state.get("audit_event_ids", [])) + [event_id],
        },
        "current_step": "governance_deny",
        "workflow_steps": steps,
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [event_id],
    }


def guardrail_check_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """Apply Bedrock Guardrails to the agent output before returning."""
    guardrail = get_guardrail_checker()
    adjudication = state.get("adjudication_result", {})

    output_text = json.dumps(adjudication)
    result = guardrail.check(output_text, source="OUTPUT")

    if result["action"] == "GUARDRAIL_INTERVENED":
        logger.warning("Bedrock Guardrail intervened on output for claim %s", state.get("claim_id"))

    steps = list(state.get("workflow_steps", []))
    steps.append("guardrail_check")

    return {
        "guardrail_result": result,
        "current_step": "guardrail_check",
        "workflow_steps": steps,
    }


def audit_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """Write final immutable audit record."""
    audit = get_audit_logger()
    claim_id = state.get("claim_id", "UNKNOWN")
    adj = state.get("adjudication_result", {})

    event_id = audit.log(
        "ADJUDICATION_COMPLETE",
        claim_id=claim_id,
        actor=state.get("actor_id", "system"),
        details={
            "status": adj.get("status"),
            "confidence": adj.get("confidence"),
            "workflow_steps": state.get("workflow_steps", []),
            "pii_entities": state.get("pii_entities_found", []),
            "hitl_resolution": state.get("hitl_resolution"),
            "guardrail_action": state.get("guardrail_result", {}).get("action"),
        },
        session_id=state.get("session_id", ""),
        outcome=adj.get("status", "unknown").upper(),
    )
    steps = list(state.get("workflow_steps", []))
    steps.append("audit")

    return {
        "current_step": "audit",
        "workflow_steps": steps,
        "audit_event_ids": list(state.get("audit_event_ids", [])) + [event_id],
    }


def response_node(state: ClaimWorkflowState) -> dict[str, Any]:
    """Assemble the final API response."""
    adj = state.get("adjudication_result", {})
    claim_id = state.get("claim_id", "UNKNOWN")

    guardrail_result = state.get("guardrail_result", {})
    output_text = guardrail_result.get("text", json.dumps(adj))

    ac = state.get("agentcore_result") or {}
    mcp_tr = state.get("mcp_tool_results") or {}
    final = {
        "correlation_id": state.get("correlation_id"),
        "claim_id": claim_id,
        "status": adj.get("status", "unknown"),
        "reject_code": adj.get("reject_code"),
        "reject_reason": adj.get("reject_reason"),
        "pricing": {
            "copay": adj.get("copay", 0.0),
            "plan_pay": adj.get("plan_pay", 0.0),
        },
        "dur_alerts": adj.get("dur_alerts", []),
        "hitl_resolution": state.get("hitl_resolution"),
        "workflow_steps": state.get("workflow_steps", []),
        "confidence": adj.get("confidence"),
        "guardrail_intervened": guardrail_result.get("action") == "GUARDRAIL_INTERVENED",
        "audit_trail": state.get("audit_event_ids", []),
        "agentcore_ms": ac.get("elapsed_ms"),
        "agentcore_used": bool(ac.get("completion")) and not ac.get("skipped"),
        "mcp_tool_results": mcp_tr if mcp_tr else None,
        "calc_claim2": state.get("calc_claim2_context"),
    }

    return {
        "final_response": final,
        "current_step": "response",
        "workflow_steps": list(state.get("workflow_steps", [])) + ["response"],
    }


# ---------------------------------------------------------------------------
# Conditional edge functions
# ---------------------------------------------------------------------------

def route_after_phi_check(state: ClaimWorkflowState) -> str:
    policy = state.get("policy_result", {})
    if policy.get("decision") == "DENY":
        return "governance_deny"
    return "supervisor"


def route_after_supervisor(state: ClaimWorkflowState) -> str:
    return state.get("routed_agent", "claims_agent")


def route_after_policy_gate(state: ClaimWorkflowState) -> str:
    policy = state.get("policy_result", {})
    decision = policy.get("decision", "ALLOW")
    if decision == "DENY":
        return "governance_deny"
    if decision in ("REQUIRE_HITL", "REQUIRE_DUAL_APPROVAL"):
        return "hitl_gate"
    return "guardrail_check"


def route_after_hitl(state: ClaimWorkflowState) -> str:
    resolution = state.get("hitl_resolution", "PENDING")
    if resolution == "DENIED":
        return "governance_deny"
    return "guardrail_check"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_claims_graph() -> StateGraph:
    graph = StateGraph(ClaimWorkflowState)

    # Register nodes
    graph.add_node("pii_scrub", pii_scrub_node)
    graph.add_node("phi_access_check", phi_access_check_node)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("agentcore_calcclaim", agentcore_calcclaim_node)
    graph.add_node("mcp_tools", mcp_tools_node)
    graph.add_node("calc_claim2_cost_core", calc_claim2_cost_core_node)
    graph.add_node("calc_claim2_copay", calc_claim2_copay_node)
    graph.add_node("calc_claim2_medicare_d", calc_claim2_medicare_d_node)
    graph.add_node("calc_claim2_margin", calc_claim2_margin_node)
    graph.add_node("calc_claim2_deductible_cap", calc_claim2_deductible_cap_node)
    graph.add_node("calc_claim2_special", calc_claim2_special_node)
    graph.add_node("calc_claim2_orchestrator", calc_claim2_orchestrator_node)
    graph.add_node("claims_agent", claims_agent_node)
    graph.add_node("formulary_agent", formulary_node)
    graph.add_node("compliance_agent", compliance_node)
    graph.add_node("policy_gate", policy_gate_node)
    graph.add_node("hitl_gate", hitl_node)
    graph.add_node("governance_deny", governance_deny_node)
    graph.add_node("guardrail_check", guardrail_check_node)
    graph.add_node("audit", audit_node)
    graph.add_node("response", response_node)

    # Edges
    graph.add_edge(START, "pii_scrub")
    graph.add_edge("pii_scrub", "phi_access_check")

    graph.add_conditional_edges(
        "phi_access_check",
        route_after_phi_check,
        {"governance_deny": "governance_deny", "supervisor": "supervisor"},
    )

    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "claims_agent": "agentcore_calcclaim",
            "formulary_agent": "formulary_agent",
            "compliance_agent": "compliance_agent",
        },
    )

    graph.add_edge("agentcore_calcclaim", "mcp_tools")
    graph.add_edge("mcp_tools", "calc_claim2_cost_core")
    graph.add_edge("calc_claim2_cost_core", "calc_claim2_copay")
    graph.add_edge("calc_claim2_copay", "calc_claim2_medicare_d")
    graph.add_edge("calc_claim2_medicare_d", "calc_claim2_margin")
    graph.add_edge("calc_claim2_margin", "calc_claim2_deductible_cap")
    graph.add_edge("calc_claim2_deductible_cap", "calc_claim2_special")
    graph.add_edge("calc_claim2_special", "calc_claim2_orchestrator")
    graph.add_edge("calc_claim2_orchestrator", "claims_agent")
    graph.add_edge("claims_agent", "policy_gate")
    graph.add_edge("formulary_agent", "policy_gate")
    graph.add_edge("compliance_agent", "policy_gate")

    graph.add_conditional_edges(
        "policy_gate",
        route_after_policy_gate,
        {
            "governance_deny": "governance_deny",
            "hitl_gate": "hitl_gate",
            "guardrail_check": "guardrail_check",
        },
    )

    graph.add_conditional_edges(
        "hitl_gate",
        route_after_hitl,
        {"governance_deny": "governance_deny", "guardrail_check": "guardrail_check"},
    )

    graph.add_edge("guardrail_check", "audit")
    graph.add_edge("audit", "response")
    graph.add_edge("response", END)
    graph.add_edge("governance_deny", END)

    return graph


def compile_claims_graph():
    """Compile and return the runnable LangGraph."""
    return build_claims_graph().compile()
