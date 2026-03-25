# CalcClaim — Architecture (detailed)

This document is the **canonical, readable** description of how the demo fits together on AWS and in code. For slide-quality graphics, use **`calclaim-architecture-aws-native.svg`** (vector); the matching **`.png`** may be older until you re-export from the SVG.

---

## 1. What you are building (one sentence)

**Systems and humans send structured claim JSON through an HTTP API; a Lambda-hosted FastAPI app runs a LangGraph workflow that scrubs PII, calls Amazon Bedrock (and optionally AgentCore and an MCP tool server), applies policy and guardrails, writes audit data, and returns an adjudication result.**

---

## 2. Three pillars: LLM Gateway, Evaluation, Governance

Enterprise agentic systems separate **how models are accessed**, **how quality is measured**, and **how risk is controlled**. CalcClaim maps all three explicitly.

### 2.1 LLM Gateway (model access plane)

**Meaning:** A controlled boundary in front of foundation models: **auth**, **routing** (which model / region / profile), **quotas**, **logging**, and **safety hooks** before and after the model call. In AWS-centric designs, **Amazon Bedrock** is often the gateway (single API to many models, guardrails, Agents, inference profiles).

| Layer | In this demo | Enterprise extensions |
|-------|----------------|------------------------|
| **Edge / API** | **Amazon API Gateway** → **Lambda** (JWT optional, throttling) | WAF, usage plans, mTLS, private API + VPC |
| **Routing & clients** | **`ModelRouter`** + **`bedrock_client.py`** (Converse, Haiku vs Sonnet) | Bedrock **inference profiles**, cross-region, cost caps |
| **Tool / agent servers** | **Bedrock AgentCore** (`InvokeAgent`), **MCP** HTTP sidecar | Additional tool backends behind the same policy |
| **Safety on I/O** | **Bedrock Guardrails** (ApplyGuardrail), PII scrub **before** LLM | Prompt filters, token limits, content classifiers |
| **Optional SaaS / proxy** | Not bundled | **LiteLLM**, **Portkey**, **Kong** + Bedrock, etc., when you need vendor-neutral routing |

**Clarification:** “LLM Gateway” here is the **logical role** (Bedrock + app router + API edge). You can add a **dedicated gateway product** later without changing the governance or eval story.

---

### 2.2 Evaluation (quality plane)

**Meaning:** **Offline** regression (golden datasets), **online** monitoring (production traces), and **human or model judges** (rubrics). Use **deterministic checks** for contracts and compliance; use **LLM-as-judge** or **Bedrock evaluation** for semantic quality.

| Layer | In this demo | Where |
|-------|----------------|--------|
| **Traces & debugging** | **LangSmith** when `LANGCHAIN_API_KEY` + tracing on | `langsmith_config.py`, LangGraph auto-instrumentation |
| **Deterministic evaluators** | PII leakage, schema, financial sanity, hedging phrases | `langsmith_config.py`, `scripts/run_llm_eval_demo.py` |
| **SLO-style metrics** | **CloudWatch EMF** (`CalcClaim/Workflow` — outcome, guardrail, AgentCore, MCP flags) | `cloudwatch_emf.py`, Lambda env `ENABLE_CLOUDWATCH_EMF` |
| **Platform telemetry** | **CloudWatch Logs**, **X-Ray** on Lambda | Terraform / CDK |

**Latest enterprise / market evaluation tools (pick by stack — not mutually exclusive):**

| Tool or service | Notes |
|-------------------|--------|
| **[LangSmith](https://smith.langchain.com/)** | Native for LangChain/LangGraph: datasets, experiments, **online evaluators**, LLM-as-judge in UI. |
| **[Amazon Bedrock](https://aws.amazon.com/bedrock/)** | **Model evaluation jobs**, **prompt management**, **invocation logging** to S3/CloudWatch — stays in AWS contract boundary. |
| **[Braintrust](https://www.braintrust.dev/)** | Popular for product teams: eval logs, scoring, CI hooks. |
| **[Patronus AI](https://www.patronus.ai/)** | Enterprise-focused LLM evaluation and safety testing. |
| **[Galileo](https://www.rungalileo.io/)** | GenAI observability + evaluation workflows. |
| **[Arize Phoenix](https://phoenix.arize.com/)** (open) / **[Arize AX](https://arize.com/)** | Traces, embeddings drift, eval for ML + LLM. |
| **[Weights & Biases Weave](https://wandb.ai/site/weave/)** | Tracing and eval tied to W&B ecosystem. |
| **[Fiddler AI](https://www.fiddler.ai/)**, **[Arthur](https://www.arthur.ai/)** | Model monitoring, governance, risk reporting. |
| **[WhyLabs](https://whylabs.ai/)** | Data / LLM observability and guardrails. |

**Practical stack for this repo:** keep **LangSmith + Bedrock** as the default story; add **Braintrust** or **Patronus** if procurement requires a separate eval vendor; use **Bedrock native eval** when everything must stay under the AWS BAA.

---

### 2.3 Governance (risk & compliance plane)

**Meaning:** **Who** may act, **what data** may flow where, **which policies** apply, **when humans** must intervene, and **what is audited** immutably.

| Control | In this demo | Where |
|---------|----------------|--------|
| **Identity** | JWT at API Gateway or FastAPI (`REQUIRE_AUTH`, `JWT_JWKS_URL`) | `jwt_verify.py`, `http_middleware.py`, Terraform/CDK |
| **PHI / PII** | Scrub before LLM; regex (+ optional Presidio); output checks | `pii_scrubber.py`, `guardrail_check`, evaluators |
| **Authorization / policy** | Inline engine + optional **OPA** (`policies/calclaim.rego`) | `policy_engine.py`, `USE_OPA` |
| **Human oversight** | HITL gate, SNS topic | `hitl_gate.py`, Terraform SNS |
| **Model output safety** | Bedrock Guardrails | `bedrock_client.py`, CDK/Terraform params |
| **Audit** | Event types, DynamoDB / demo store | `audit_logger.py` |
| **Secrets & SDLC** | GitHub secrets, Bandit, pip-audit, Dependabot | `.github/` |

**Enterprise target:** **Amazon Verified Permissions (Cedar)**, **Macie** for S3, **KMS**, **CloudTrail** org trails — wire progressively; the diagram and this doc show the **logical** governance slots.

---

## 3. End-to-end request path (REST)

**Diagrams:** [calclaim-request-flow.drawio](calclaim-request-flow.drawio) (diagrams.net — edit and export PNG/SVG/PDF); [calclaim-request-flow.md](calclaim-request-flow.md) (Mermaid for GitHub preview).

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

## 4. Two external surfaces (both valid in production)

### 4.1 REST — API Gateway → Lambda → FastAPI

- **Purpose:** Integrations, portals, batch jobs, mobile apps.
- **Contract:** OpenAPI-style JSON over HTTPS.
- **IaC:** `infrastructure/cdk/stacks/api_stack.py` or `terraform/` (HTTP API + Lambda zip).

### 4.2 MCP — separate service (streamable HTTP or stdio)

- **Purpose:** IDE agents, copilots, and other MCP-capable clients that need **structured tools** without owning the full claim schema.
- **Deployment:** Typically **ECS/Fargate**, **EKS**, or a small **EC2** / internal **ALB** — **not** inside the adjudication Lambda zip (keeps cold starts small and concerns separated).
- **LangGraph link:** When `CALCLAIM_MCP_URL` is set (Python 3.10+ and `mcp` installed), the graph’s **`mcp_tools`** node calls the MCP server over HTTP for tools such as `formulary_tier_lookup`. See `src/utils/mcp_workflow_client.py`.

---

## 5. LangGraph workflow (logical order)

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

### 5a. Demo API — five pillars (live status)

For stakeholder demos, the service exposes **`GET /demo/pillars`**, **`GET /demo/pillars/{pillar}`**, and **`POST /demo/governance/policy-probe`**. See **`docs/DEMO_PILLARS.md`**, **`src/utils/pillar_status.py`**, routes in **`lambda/handler.py`** (OpenAPI tag **demo-pillars**).

---

## 6. AWS services (what maps to what)

| Concern | AWS service (target / demo) | Notes |
|--------|------------------------------|--------|
| Ingress | **Amazon API Gateway** (HTTP API) | Auth, throttling, route to Lambda |
| Compute | **AWS Lambda** | Runs FastAPI + LangGraph + boto3 Bedrock calls |
| LLM | **Amazon Bedrock** | Claude via Converse; guardrails; AgentCore via Agents Runtime |
| Persistence | **Amazon DynamoDB** | Claims / audit / sessions (table names vary by IaC) |
| HITL | **Amazon SNS** (+ optional SQS) | Human-in-the-loop notifications |
| Object storage | **Amazon S3** | WORM / long retention patterns in CDK reference |
| LLM observability (SaaS) | **LangSmith** | Traces from LangChain/LangGraph when env vars set — **not** a replacement for CloudWatch |
| Platform observability | **CloudWatch** | Logs, metrics, alarms, EMF (`cloudwatch_emf.py`); see AWS-native diagram |
| Events | *(optional, not on reference diagram)* | e.g. EventBridge for ops / SIEM when you add it |

**Verified Permissions / Cedar** in diagrams is the **enterprise** direction; this repo uses an **inline policy engine** unless you wire an external OPA or AVP.

---

## 7. Observability and LLM evaluation (implementation detail)

*Pillar-level story is in **§2** (LLM Gateway, Evaluation, Governance). This section lists concrete AWS and code hooks.*

### AWS (platform)

1. **CloudWatch Logs** — Lambda `/aws/lambda/calclaim-api` with configurable retention (Terraform `lambda_log_retention_days`).  
2. **AWS X-Ray** — **Active tracing** on Lambda (Terraform `enable_xray_tracing`, CDK `Tracing.ACTIVE` + `AWSXRayDaemonWriteAccess`) for service map and latency segments (API Gateway → Lambda → downstream calls). Enable **AWS Distro for OpenTelemetry (ADOT)** Lambda layer later if you want OTLP export to multiple backends.  
3. **Embedded Metric Format (EMF)** — `ENABLE_CLOUDWATCH_EMF=true` emits structured metric lines (`CalcClaim/Workflow` namespace) for adjudication outcomes without PHI in dimensions (`src/utils/cloudwatch_emf.py`).  
4. **Amazon Bedrock** — Turn on **model invocation logging** (CloudWatch or S3) in the Bedrock console for raw request/response audit in regulated environments (separate from this repo’s code).  
5. **CloudWatch Application Signals** — For deeper APM/SLOs on multiple services, add the Application Signals / Synthetics patterns in CDK when the workload grows beyond a single Lambda.  
6. **CloudTrail** — Control-plane API audit (who changed IAM, Bedrock, etc.).

### LLM quality (application)

1. **LangSmith** — Traces, **Datasets**, and **Evaluations** (including online evaluators and LLM-as-judge in the LangSmith product UI). This repo supplies deterministic evaluators in `langsmith_config.py` (PII leakage, schema, financial sanity, hedging phrases) and `scripts/run_llm_eval_demo.py` for local runs.  
2. **Market direction** — Combine **deterministic checks** (schema, money, regex PII) with **model-based judges** in LangSmith or **Amazon Bedrock evaluation jobs** for rubric scoring on production-like traces.  
3. **OpenTelemetry** — Dependencies include `opentelemetry-*`; set `OTEL_EXPORTER_OTLP_ENDPOINT` (and wire a tracer in app startup) when you standardize on OTLP for multi-vendor backends.

---

## 8. Optional document ingestion (not implemented — design hook)

When claims or PA forms arrive as **scanned PDFs or images**, a common pattern is:

**S3 (upload)** → **Amazon Textract** (OCR + forms/tables) → **Lambda** (map fields to claim JSON) → **same REST endpoint** as structured claims.

That path is **not** coded in this demo; the architecture diagram shows it as an **optional extension** so stakeholders see where OCR fits relative to CalcClaim.

---

## 9. Repository map (quick)

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

## 10. Diagram legend (AWS-native SVG)

- **Bottom band (three boxes)** — **§2 pillars**: LLM Gateway (blue), Evaluation (amber), Governance (green).  
- **Solid arrows** — Main adjudication / data flow (client → API → Lambda → graph → Bedrock / DDB / response).  
- **Orange dashed** — LangSmith trace export from application code.  
- **Gray dashed** — Telemetry to **CloudWatch** (logs / EMF metrics); LangSmith uses the orange dashed line.  
- **Green dashed** — Optional **S3 + Textract** document pipeline into structured claims.

Re-export **`calclaim-architecture-aws-native.png`** from the SVG in Figma, Inkscape, or `rsvg-convert` if you need an updated raster for decks.

---

## 11. Enterprise controls (implemented in repo)

| Area | What | Configuration |
|------|------|----------------|
| **Identity** | Optional **JWT** in FastAPI (`REQUIRE_AUTH` + `JWT_JWKS_URL`, optional `JWT_AUDIENCE` / `JWT_ISSUER`) or **API Gateway JWT authorizer** (Terraform `enable_jwt_authorizer`, CDK context `jwtIssuer` / `jwtAudience`) + **`TRUST_API_GATEWAY_AUTH=true`** so Lambda does not re-verify. | `.env.example`, `terraform/variables.tf`, `infrastructure/cdk/stacks/api_stack.py` |
| **CORS** | `CORS_ALLOW_ORIGINS` for FastAPI; Terraform `cors_allow_origins` for API Gateway. | `lambda/handler.py`, `terraform/api_gateway.tf` |
| **Governance** | **OPA** HTTP when `USE_OPA=true` and `OPA_SERVER_URL` set — loads **`policies/calclaim.rego`** (claim_access, bulk, phi, formulary). | `src/governance/policy_engine.py`, `policies/calclaim.rego` |
| **Observability** | **`LOG_FORMAT=json`** adds `correlation_id` to log lines; **`X-Correlation-ID`** / **`X-Request-ID`** middleware; optional **X-Ray** via `aws-xray-sdk` + `AWS_XRAY_TRACING_ENABLED` / `XRAY_PATCH_SDK`. | `src/utils/enterprise_logging.py`, `src/utils/http_middleware.py`, `requirements-optional.txt` |
| **SDLC** | **Bandit** + **pip-audit** in CI; **Dependabot** for pip + GitHub Actions. | `.github/workflows/deploy-aws.yml` (security job), `.github/dependabot.yml` |
| **MCP** | **`MCP_ALLOWED_HOSTS`**, **`MCP_ALLOWED_SCHEMES`**, optional **`MCP_BEARER_TOKEN`** (when supported by installed `mcp` SDK). | `src/utils/mcp_workflow_client.py` |
