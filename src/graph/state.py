"""
LangGraph state schema for the CalcClaim multi-agent workflow.
"""

from __future__ import annotations

from typing import Any, Annotated, Optional
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class ClaimWorkflowState(TypedDict, total=False):
    # Incoming request
    correlation_id: str  # API request / log correlation (no PHI)
    claim_id: str
    session_id: str
    raw_claim: dict[str, Any]          # original, may contain PII
    safe_claim: dict[str, Any]         # PII-scrubbed version for LLM context
    actor_role: str                    # e.g. "claims_processor", "supervisor"
    actor_id: str
    action: str                        # e.g. "adjudicate", "reverse", "query"

    # Agent messages (append-only via add_messages)
    messages: Annotated[list[BaseMessage], add_messages]

    # Supervisor routing
    intent: str                        # classified intent
    routed_agent: str                  # "claims_agent" | "formulary_agent" | etc.

    # Amazon Bedrock AgentCore (CalcClaim tool server — invoke_agent)
    agentcore_result: dict[str, Any]   # completion, trace, elapsed_ms, session_id

    # MCP (Model Context Protocol) tool results — optional enrichment before claims_agent
    mcp_tool_results: dict[str, Any]

    # calcClaim2 modular pipeline (deterministic demo — mirrors C++ component stages)
    calc_claim2_context: dict[str, Any]  # cost, copay, medicare_d, margin, deductible_cap, special, orchestrator, return_code

    # Governance outputs
    pii_entities_found: list[str]
    policy_result: dict[str, Any]
    guardrail_result: dict[str, Any]
    hitl_request_id: Optional[str]
    hitl_resolution: Optional[str]     # "APPROVED" | "DENIED"

    # Adjudication outputs
    adjudication_result: dict[str, Any]
    adjudication_reasoning: str

    # Step tracking
    current_step: str
    workflow_steps: list[str]
    errors: list[str]

    # LaunchDarkly (optional) — effective booleans merged with USE_* env at request time
    feature_flags: dict[str, Any]

    # Final response
    final_response: dict[str, Any]
    audit_event_ids: list[str]
