# CalcClaim MCP servers

## Where this sits vs API Gateway

| Surface | Role | Who calls it |
|--------|------|----------------|
| **Amazon API Gateway → Lambda → FastAPI** | Canonical **REST** API: adjudication, reversal, HITL, OpenAPI `/docs` | Apps, partners, ESB, batch jobs |
| **This MCP server** | **Model Context Protocol** tools for **agents** (formulary hints, ID validation, “where is REST?”) | Copilots, Claude Desktop, Bedrock Agent tool plugins, LangGraph MCP client |

Both should exist in production: **Gateway** for operational HTTP contracts; **MCP** for agent-native, schema-first tools (often inside the VPC on streamable HTTP + OAuth per MCP auth spec).

## Run locally (stdio)

```bash
cd mcp_servers
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m calclaim_mcp
```

## Run for VPC / container (streamable HTTP)

```bash
export MCP_TRANSPORT=streamable-http
export MCP_HOST=0.0.0.0
export MCP_PORT=8765
export CALCLAIM_API_BASE_URL=https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com
python -m calclaim_mcp
```

Put **TLS + auth** (e.g. ALB + OIDC, or API Gateway in front of the MCP HTTP listener) before exposing outside the VPC.

## Docker

```bash
cd mcp_servers
docker build -t calclaim-mcp .
docker run -p 8765:8765 -e MCP_TRANSPORT=streamable-http -e CALCLAIM_API_BASE_URL=https://... calclaim-mcp
```

## Tools (demo)

- `formulary_tier_lookup` — synthetic tier/PA hints for a few NDCs  
- `list_demo_formulary_drugs`  
- `validate_claim_id` — `CLM-` + 12 hex format only  
- `calcclaim_rest_surface` — documents REST base URL from `CALCLAIM_API_BASE_URL`

Replace `tools_domain.py` with real formulary / eligibility integrations in production.

## LangGraph integration

The main workflow calls the MCP server **between AgentCore and the claims agent** when:

- `CALCLAIM_MCP_URL` is set (full streamable-HTTP URL, usually ending in `/mcp`),
- `USE_MCP_TOOLS` is not `false`,
- Python **≥ 3.10** and the `mcp` package is installed (`pip install -r requirements.txt` from repo root).

It invokes the `formulary_tier_lookup` tool and passes the JSON into the claims LLM prompt. Audit event: `MCP_TOOLS_INVOKED`.
