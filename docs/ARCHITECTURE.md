# CalcClaim — Architecture (detailed)

This document is the **canonical, readable** description of how the demo fits together on AWS and in code. For slide-quality graphics, use **`calclaim-architecture-aws-native.svg`** (vector); the matching **`.png`** may be older until you re-export from the SVG.

---

## 1. What you are building (one sentence)

**Systems and humans send structured claim JSON through an HTTP API; a Lambda-hosted FastAPI app runs a LangGraph workflow that scrubs PII, calls Amazon Bedrock (and optionally AgentCore and an MCP tool server), applies policy and guardrails, writes audit data, and returns an adjudication result.**

---

## 2. End-to-end request path (REST)

| Step | What happens | Where in repo |
|------|----------------|---------------|
| 1 | Client calls **HTTP API** (e.g. `POST /claims/adjudicate`) | `lambda/handler.py`, OpenAPI routes |
| 2 | **API Gateway** forwards to **Lambda** (Mangum ASGI adapter) | CDK `api_stack` / Terraform API + Lambda |
| 3 | **FastAPI** validates input and invokes the **LangGraph** compiled graph | `src/graph/claims_workflow.py` |
| 4 | Graph nodes run in process: PII scrub → governance → supervisor → specialists → policy → optional HITL → guardrails → audit → response | Same file + `src/governance/*` |
| 5 | **Bedrock** is called from Python (Converse for chat models, `invoke_agent` for AgentCore, ApplyGuardrail when configured) | `src/utils/bedrock_client.py` |
| 6 | **DynamoDB** (and optionally S3) persist audit and session data when not in demo mode | `src/governance/audit_logger.py`, CDK `core_stack` / Terraform tables |
| 7 | JSON response returns to the client | `response_node` → API response |

**Important:** The **MCP server is not** the same as API Gateway. Full adjudication stays on **REST**. MCP exposes **tools** (formulary lookup, validation, pointer to REST) for agentic clients.

---

## 3. Two external surfaces (both valid in production)

### 3.1 REST — API Gateway → Lambda → FastAPI

- **Purpose:** Integrations, portals, batch jobs, mobile apps.
- **Contract:** OpenAPI-style JSON over HTTPS.
- **IaC:** `infrastructure/cdk/stacks/api_stack.py` or `terraform/` (HTTP API + Lambda zip).

### 3.2 MCP — separate service (streamable HTTP or stdio)

- **Purpose:** IDE agents, copilots, and other MCP-capable clients that need **structured tools** without owning the full claim schema.
- **Deployment:** Typically **ECS/Fargate**, **EKS**, or a small **EC2** / internal **ALB** — **not** inside the adjudication Lambda zip (keeps cold starts small and concerns separated).
- **LangGraph link:** When `CALCLAIM_MCP_URL` is set (Python 3.10+ and `mcp` installed), the graph’s **`mcp_tools`** node calls the MCP server over HTTP for tools such as `formulary_tier_lookup`. See `src/utils/mcp_workflow_client.py`.

---

## 4. LangGraph workflow (logical order)

1. **`pii_scrub`** — Mask PHI before any LLM sees the payload.  
2. **`phi_access_check`** — HIPAA-style purpose / access check.  
3. **`supervisor`** — Routes to claims, formulary, or compliance path.  
4. **`agentcore_calcclaim`** (claims path) — Optional **Bedrock Agents** `InvokeAgent` (mock or skip if misconfigured).  
5. **`mcp_tools`** (claims path) — Optional MCP HTTP tools.  
6. **`claims_agent` / `formulary_agent` / `compliance_agent`** — Bedrock Converse (Sonnet/Haiku per node).  
7. **`policy_gate`** — OPA-style rules in code (`policy_engine.py`).  
8. **`hitl_gate`** — SNS / demo auto-resolve when policy requires human review.  
9. **`guardrail_check`** — Bedrock Guardrails on model output when configured.  
10. **`audit`** — Immutable audit event trail.  
11. **`response`** — Assemble `final_response` (may include `agentcore_*`, `mcp_tool_results`).

Full numbered list also appears in **`REPORT.md`** §4.

---

## 5. AWS services (what maps to what)

| Concern | AWS service (target / demo) | Notes |
|--------|------------------------------|--------|
| Ingress | **Amazon API Gateway** (HTTP API) | Auth, throttling, route to Lambda |
| Compute | **AWS Lambda** | Runs FastAPI + LangGraph + boto3 Bedrock calls |
| LLM | **Amazon Bedrock** | Claude via Converse; guardrails; AgentCore via Agents Runtime |
| Persistence | **Amazon DynamoDB** | Claims / audit / sessions (table names vary by IaC) |
| HITL | **Amazon SNS** (+ optional SQS) | Human-in-the-loop notifications |
| Object storage | **Amazon S3** | WORM / long retention patterns in CDK reference |
| LLM observability (SaaS) | **LangSmith** | Traces from LangChain/LangGraph when env vars set — **not** a replacement for CloudWatch |
| Platform observability | **CloudWatch**, **X-Ray**, **CloudTrail** | Logs, metrics, traces, API audit |
| Events | **EventBridge** (optional) | Ops / SIEM integration pattern |

**Verified Permissions / Cedar** in diagrams is the **enterprise** direction; this repo uses an **inline policy engine** unless you wire an external OPA or AVP.

---

## 6. Observability: two layers

1. **AWS-native** — Lambda logs and metrics in **CloudWatch**, distributed traces with **X-Ray** (when enabled), control-plane history in **CloudTrail**.  
2. **LLM / chain** — **LangSmith** for run-level debugging, datasets, and eval hooks (`langsmith_config.py`). Teams use both: CloudWatch for SLOs and incidents, LangSmith for prompt and graph behavior.

---

## 7. Optional document ingestion (not implemented — design hook)

When claims or PA forms arrive as **scanned PDFs or images**, a common pattern is:

**S3 (upload)** → **Amazon Textract** (OCR + forms/tables) → **Lambda** (map fields to claim JSON) → **same REST endpoint** as structured claims.

That path is **not** coded in this demo; the architecture diagram shows it as an **optional extension** so stakeholders see where OCR fits relative to CalcClaim.

---

## 8. Repository map (quick)

| Path | Role |
|------|------|
| `src/graph/claims_workflow.py` | LangGraph definition and nodes |
| `src/graph/state.py` | Workflow state schema |
| `src/utils/bedrock_client.py` | Bedrock, guardrails, AgentCore |
| `src/utils/mcp_workflow_client.py` | MCP HTTP client for `mcp_tools` node |
| `lambda/handler.py` | FastAPI + Mangum |
| `mcp_servers/calclaim_mcp/` | FastMCP tool server |
| `infrastructure/cdk/` | Full CDK stacks |
| `terraform/` | Lean Lambda + API GW + DDB + SNS |
| `docs/calclaim-architecture-aws-native.svg` | AWS-native diagram (editable) |
| `docs/calclaim-architecture-flow.png` | LangGraph-centric flow (image) |

---

## 9. Diagram legend (AWS-native SVG)

- **Solid arrows** — Main adjudication / data flow (client → API → Lambda → graph → Bedrock / DDB / response).  
- **Orange dashed** — LangSmith trace export from application code.  
- **Gray dashed** — Telemetry and audit paths (logs, metrics, X-Ray, Trail).  
- **Green dashed** — Optional **S3 + Textract** document pipeline into structured claims.

Re-export **`calclaim-architecture-aws-native.png`** from the SVG in Figma, Inkscape, or `rsvg-convert` if you need an updated raster for decks.

---

## 10. Enterprise controls (implemented in repo)

| Area | What | Configuration |
|------|------|----------------|
| **Identity** | Optional **JWT** in FastAPI (`REQUIRE_AUTH` + `JWT_JWKS_URL`, optional `JWT_AUDIENCE` / `JWT_ISSUER`) or **API Gateway JWT authorizer** (Terraform `enable_jwt_authorizer`, CDK context `jwtIssuer` / `jwtAudience`) + **`TRUST_API_GATEWAY_AUTH=true`** so Lambda does not re-verify. | `.env.example`, `terraform/variables.tf`, `infrastructure/cdk/stacks/api_stack.py` |
| **CORS** | `CORS_ALLOW_ORIGINS` for FastAPI; Terraform `cors_allow_origins` for API Gateway. | `lambda/handler.py`, `terraform/api_gateway.tf` |
| **Governance** | **OPA** HTTP when `USE_OPA=true` and `OPA_SERVER_URL` set — loads **`policies/calclaim.rego`** (claim_access, bulk, phi, formulary). | `src/governance/policy_engine.py`, `policies/calclaim.rego` |
| **Observability** | **`LOG_FORMAT=json`** adds `correlation_id` to log lines; **`X-Correlation-ID`** / **`X-Request-ID`** middleware; optional **X-Ray** via `aws-xray-sdk` + `AWS_XRAY_TRACING_ENABLED` / `XRAY_PATCH_SDK`. | `src/utils/enterprise_logging.py`, `src/utils/http_middleware.py`, `requirements-optional.txt` |
| **SDLC** | **Bandit** + **pip-audit** workflow; **Dependabot** for pip + GitHub Actions. | `.github/workflows/security.yml`, `.github/dependabot.yml` |
| **MCP** | **`MCP_ALLOWED_HOSTS`**, **`MCP_ALLOWED_SCHEMES`**, optional **`MCP_BEARER_TOKEN`** (when supported by installed `mcp` SDK). | `src/utils/mcp_workflow_client.py` |
