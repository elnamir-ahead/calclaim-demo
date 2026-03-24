"""
AWS Lambda entry point — wraps FastAPI app via Mangum (ASGI adapter).
Deployed behind API Gateway (HTTP API) — the canonical REST surface for CalcClaim.

Agent-facing MCP tools live in a separate process: ``mcp_servers/calclaim_mcp``
(stdio or streamable HTTP), not in this Lambda package.

Event sources:
  - POST /claims/adjudicate   → run full CalcClaim workflow
  - POST /claims/reverse      → run reversal workflow
  - GET  /claims/{claim_id}/status  → status query
  - GET  /claims/{claim_id}/audit   → audit trail
  - GET  /health              → health check
"""

from __future__ import annotations

import os
import uuid
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from mangum import Mangum

# Align LangSmith tracing with API key before langchain reads env (Lambda + local)
from src.utils.env_bootstrap import bootstrap_langchain_env

bootstrap_langchain_env()

from src.utils.enterprise_logging import configure_logging, maybe_patch_xray

configure_logging()
maybe_patch_xray()

from src.utils.langsmith_config import configure_tracing

configure_tracing()

from src.graph.claims_workflow import compile_claims_graph
from src.governance import get_audit_logger, get_hitl_gate
from src.data.fake_data import generate_demo_dataset
from src.utils.request_context import get_correlation_id
from src.utils.http_middleware import BearerAuthMiddleware, CorrelationIdMiddleware

logger = logging.getLogger(__name__)


def _cors_allow_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]

# ---------------------------------------------------------------------------
# App + graph (compiled once at cold-start)
# ---------------------------------------------------------------------------

_graph = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graph
    logger.info("Compiling CalcClaim LangGraph...")
    _graph = compile_claims_graph()
    logger.info("CalcClaim graph ready.")
    yield


app = FastAPI(
    title="CalcClaim — Navitus Enterprise Agentic AI",
    description="Pharmacy benefit claim adjudication powered by Amazon Bedrock + LangGraph",
    version="2.0.0",
    lifespan=lifespan,
)

# Order: last added = outermost on request. CORS outermost, then correlation, then JWT.
app.add_middleware(BearerAuthMiddleware)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class AdjudicateRequest(BaseModel):
    claim_id: Optional[str] = None
    claim: Optional[dict] = None    # pass raw claim or use demo data
    actor_id: str = "system"
    actor_role: str = "claims_processor"
    action: str = "adjudicate"
    use_demo_claim: bool = False           # generate a random demo claim


class ReversalRequest(BaseModel):
    claim_id: str
    reversal_reason: str
    actor_id: str
    actor_role: str = "supervisor"


class HITLResolveRequest(BaseModel):
    request_id: str
    resolution: str  # APPROVED | DENIED
    resolved_by: str
    note: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_graph():
    global _graph
    if _graph is None:
        _graph = compile_claims_graph()
    return _graph


async def _run_workflow(state: dict[str, Any]) -> dict[str, Any]:
    graph = _get_graph()
    result = await graph.ainvoke(state)
    return result.get("final_response", {})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    from src.utils.jwt_verify import describe_auth_mode

    return {
        "status": "healthy",
        "service": "calclaim-demo",
        "version": "2.0.0",
        "auth_mode": describe_auth_mode(),
    }


@app.post("/claims/adjudicate")
async def adjudicate_claim(req: AdjudicateRequest):
    """Run the full CalcClaim adjudication workflow."""
    claim = req.claim
    claim_id = req.claim_id

    if req.use_demo_claim or claim is None:
        dataset = generate_demo_dataset(n_members=1, claims_per_member=1)
        claim = dataset["claims"][0]
        claim_id = claim["claim_id"]
    elif claim_id is None:
        claim_id = claim.get("claim_id", f"CLM-{uuid.uuid4().hex[:12].upper()}")

    session_id = str(uuid.uuid4())
    cid = get_correlation_id() or ""

    initial_state = {
        "correlation_id": cid,
        "claim_id": claim_id,
        "session_id": session_id,
        "raw_claim": claim,
        "actor_id": req.actor_id,
        "actor_role": req.actor_role,
        "action": req.action,
        "messages": [],
        "workflow_steps": [],
        "errors": [],
        "audit_event_ids": [],
    }

    try:
        result = await _run_workflow(initial_state)
        return {"success": True, "session_id": session_id, "result": result}
    except Exception as exc:
        logger.exception("Adjudication workflow error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/claims/reverse")
async def reverse_claim(req: ReversalRequest):
    """Initiate a claim reversal (triggers HITL dual-approval gate)."""
    session_id = str(uuid.uuid4())
    dataset = generate_demo_dataset(n_members=1, claims_per_member=1)
    claim = dataset["claims"][0]
    claim["claim_id"] = req.claim_id

    cid = get_correlation_id() or ""
    initial_state = {
        "correlation_id": cid,
        "claim_id": req.claim_id,
        "session_id": session_id,
        "raw_claim": claim,
        "actor_id": req.actor_id,
        "actor_role": req.actor_role,
        "action": "reverse",
        "messages": [],
        "workflow_steps": [],
        "errors": [],
        "audit_event_ids": [],
    }

    try:
        result = await _run_workflow(initial_state)
        return {"success": True, "session_id": session_id, "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/claims/{claim_id}/audit")
def get_audit_trail(claim_id: str):
    """Retrieve the immutable audit trail for a claim."""
    audit = get_audit_logger()
    trail = audit.get_claim_trail(claim_id)
    return {"claim_id": claim_id, "events": trail, "count": len(trail)}


@app.get("/hitl/pending")
def get_hitl_pending():
    """List pending HITL review requests."""
    hitl = get_hitl_gate()
    pending = hitl.get_pending()
    return {"pending": [r.to_dict() for r in pending], "count": len(pending)}


@app.post("/hitl/resolve")
def resolve_hitl(req: HITLResolveRequest):
    """Resolve a HITL review request (reviewer action)."""
    hitl = get_hitl_gate()
    resolved = hitl.resolve(
        request_id=req.request_id,
        resolution=req.resolution,
        resolved_by=req.resolved_by,
        note=req.note,
    )
    if not resolved:
        raise HTTPException(status_code=404, detail=f"HITL request {req.request_id} not found")
    return {"success": True, "request": resolved.to_dict()}


@app.post("/demo/batch")
async def demo_batch(n_claims: int = 5):
    """Run a batch of demo claims through the workflow."""
    if n_claims > 20:
        raise HTTPException(status_code=400, detail="Max 20 claims per demo batch")

    dataset = generate_demo_dataset(n_members=n_claims, claims_per_member=1)
    results = []

    cid = get_correlation_id() or ""
    for claim in dataset["claims"]:
        session_id = str(uuid.uuid4())
        state = {
            "correlation_id": cid,
            "claim_id": claim["claim_id"],
            "session_id": session_id,
            "raw_claim": claim,
            "actor_id": "demo-system",
            "actor_role": "claims_processor",
            "action": "adjudicate",
            "messages": [],
            "workflow_steps": [],
            "errors": [],
            "audit_event_ids": [],
        }
        try:
            result = await _run_workflow(state)
            results.append({"claim_id": claim["claim_id"], "success": True, "result": result})
        except Exception as exc:
            results.append({"claim_id": claim["claim_id"], "success": False, "error": str(exc)})

    return {
        "total": n_claims,
        "processed": len(results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

handler = Mangum(app, lifespan="on")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("lambda.handler:app", host="0.0.0.0", port=8000, reload=True)
