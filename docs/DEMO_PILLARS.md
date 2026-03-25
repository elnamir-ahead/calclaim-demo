# Five enterprise pillars — live demo

The API exposes **coded** endpoints so you can walk through **LLM Gateway**, **Evaluation**, **Governance**, **MCP**, and **Observability**.

| Pillar | Endpoint / action |
|--------|---------------------|
| All five | `GET /demo/pillars` |
| One pillar | `GET /demo/pillars/{llm_gateway\|evaluation\|governance\|mcp\|observability}` |
| Governance (instant) | `POST /demo/governance/policy-probe` |
| Full stack | `POST /claims/adjudicate` with `use_demo_claim: true` |
| Health + links | `GET /health` (includes `demo` when deployed) |

Open **`/docs`** — tag **demo-pillars**.

Terraform: `agentcore_agent_id`, `calclaim_mcp_url`, `use_mcp_tools` in `terraform/variables.tf`.
