"""
CalcClaim MCP server — enterprise agent tool plane.

- **Not** a replacement for API Gateway: REST adjudication stays at the HTTP API
  (Lambda + FastAPI) in front of LangGraph.
- **MCP** exposes structured tools for copilots, IDE agents, and Bedrock Agent
  tool connectors that speak Model Context Protocol.

Run (stdio — Claude Desktop / local agents):
  cd mcp_servers && pip install -r requirements.txt
  PYTHONPATH=.. python -m calclaim_mcp.server

Run (streamable HTTP — behind ALB/API Gateway in VPC):
  MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8765 python -m calclaim_mcp.server

Requires Python >= 3.10.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from calclaim_mcp.tools_domain import (
    demo_ndc_list,
    formulary_lookup,
    validate_claim_id_format,
)

_use_streamable_http = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower() in (
    "streamable-http",
    "streamable_http",
    "http",
)

mcp = FastMCP(
    "CalcClaim Enterprise Tools",
    json_response=True,
    stateless_http=_use_streamable_http,
    instructions=(
        "Pharmacy-benefit CalcClaim tools (demo data). "
        "For full claim adjudication, reversals, and audit REST APIs, call the "
        "CalcClaim HTTP API served behind Amazon API Gateway (see tool "
        "calcclaim_rest_surface)."
    ),
)


@mcp.tool()
def formulary_tier_lookup(ndc: str, plan_code: str = "commercial_ppo") -> dict:
    """Look up demo formulary tier / PA hints for an NDC (11- or 8-digit, hyphens optional)."""
    return formulary_lookup(ndc, plan_code)


@mcp.tool()
def list_demo_formulary_drugs() -> list[dict]:
    """Return the small set of NDCs bundled in this demo MCP server."""
    return demo_ndc_list()


@mcp.tool()
def validate_claim_id(claim_id: str) -> dict:
    """Validate CLM-xxxxxxxxxxxx ID format (no PHI lookup)."""
    return validate_claim_id_format(claim_id)


@mcp.tool()
def calcclaim_rest_surface() -> dict:
    """
    Where the REST API lives (API Gateway + Lambda).

    Agents should use HTTP POST /claims/adjudicate etc. for full workflow runs;
    use MCP tools here for lightweight formulary / validation helpers.
    """
    base = os.environ.get("CALCLAIM_API_BASE_URL", "").rstrip("/")
    return {
        "role": "canonical REST + OpenAPI for full CalcClaim workflow",
        "api_base_url_set": bool(base),
        "api_base_url": base or None,
        "hint": "Set CALCLAIM_API_BASE_URL to your API Gateway stage URL (e.g. https://xxx.execute-api.us-east-1.amazonaws.com).",
        "example_paths": {
            "health": "GET /health",
            "adjudicate": "POST /claims/adjudicate",
            "openapi": "GET /docs",
        },
    }


def main() -> None:
    if _use_streamable_http:
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("MCP_PORT", "8765"))
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
