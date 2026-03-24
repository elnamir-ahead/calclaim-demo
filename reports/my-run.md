# CalcClaim demo report

> **When:** 2026-03-24 15:06:14 UTC  
> **Scenario:** **mixed** — Typical mix of drugs, tiers, and plans (default demo).

## At a glance

This run tried **3** synthetic claim(s). **None finished successfully** — each one raised an exception before a normal adjudication outcome was returned. Scroll to **Per claim** for what broke and **Environment** for likely fixes.

Synthetic batch total plan liability (fake dollars): **$317.00**.

---

## Quick stats

| Question | Answer |
|----------|--------|
| How many claims? | 3 |
| Finished without an exception? | 0 yes, 3 no |
| Approved (or approved with PA)? | 0 |
| Stopped by governance (`denied`)? | 0 |
| Needs review / unknown outcome? | 0 |
| Reject-style outcomes? | 0 |
| Audit rows captured (demo memory)? | 9 |

*“Governance denied” means the workflow returned before a normal paid/reject decision, often due to policy or guardrail configuration.*

---

## How to read this report

1. **Per claim** — One section per claim: what was simulated, then outcome or error.
2. **Environment** — Shows non-secret settings; mismatches here usually explain errors.
3. **Troubleshooting** — Common log messages and what to change in `.env` or AWS.
4. **Audit trail** — Short table of what the demo recorded (PII scrub, governance, etc.).
5. **`demo_output.json`** — Same run in JSON for tools and diffing.

---

## Environment (non-secret)

| Variable | Value | Why it matters |
|----------|-------|----------------|
| `AWS_REGION` | `us-east-1` | Default AWS region for SDK calls. |
| `BEDROCK_REGION` | `us-east-1` | Region for Bedrock Converse / guardrails. |
| `BEDROCK_GUARDRAIL_ID` | `your-guardrail-id` | Bedrock Guardrail resource ID (empty = skip guardrail on Converse). |
| `BEDROCK_GUARDRAIL_VERSION` | `DRAFT` | Guardrail version (e.g. DRAFT or version number). |
| `AGENTCORE_AGENT_ID` | `your-agentcore-agent-id` | Bedrock AgentCore agent ID (short alphanumeric). |
| `AGENTCORE_AGENT_ALIAS_ID` | `your-agentcore-alias-id` | Agent alias ID for InvokeAgent. |
| `USE_AGENTCORE` | *(not set)* | If true, graph may call AgentCore (needs real IDs). |
| `LANGCHAIN_TRACING_V2` | `false` | Send LangGraph/LangChain spans to LangSmith when true. |
| `DEMO_MODE` | `true` | Demo shortcuts (e.g. in-memory audit vs DynamoDB). |

---

## Troubleshooting cheat sheet

#### Invalid Bedrock Guardrail ID

**Symptom:** `ValidationException` mentioning *guardrail identifier is invalid* on Converse. **Fix:** Remove `BEDROCK_GUARDRAIL_ID` from `.env` for local runs, or set a real ID from your account.

#### Guardrail input format error

**Symptom:** `ApplyGuardrail` *incorrect format*. **Fix:** Same as above — bad ID/version often causes this.

#### AgentCore / InvokeAgent validation

**Symptom:** Long message about `agentId` / `agentAliasId` pattern or length. **Fix:** Use real AgentCore IDs from the console, or turn off AgentCore so the graph skips that node.

#### LangSmith HTTP 403

**Symptom:** Error posting to `api.smith.langchain.com`. **Fix:** Disable tracing (`LANGCHAIN_TRACING_V2=false`) or supply a valid `LANGCHAIN_API_KEY`.

#### Very few workflow steps, outcome `denied`

**Symptom:** ~3 audit events and immediate `denied`. **Fix:** Often PHI purpose mapping — ensure `adjudicate` maps to an allowed purpose in policy code.

---

## Per claim

### Claim 1: `CLM-002D0007B9BA`

**Drug:** Lipitor 20mg (brand, tier 3, $145.00) · **Plan:** Commercial PPO Gold

**Result:** **Failed** — AgentCore IDs are placeholders

AWS expects short alphanumeric agent and alias IDs. Replace `AGENTCORE_AGENT_ID` and `AGENTCORE_AGENT_ALIAS_ID` with real values, or disable AgentCore in config so the demo does not call InvokeAgent.

**Raw message** (for support / search):

```text
An error occurred (ValidationException) when calling the InvokeAgent operation: 4 validation errors detected: Value 'your-agentcore-agent-id' at 'agentId' failed to satisfy constraint: Member must satisfy regular expression pattern: [0-9a-zA-Z]+; Value 'your-agentcore-agent-id' at 'agentId' failed to satisfy constraint: Member must have length less than or equal to 10; Value 'your-agentcore-alias-id' at 'agentAliasId' failed to satisfy constraint: Member must satisfy regular expression pattern: [0-9a-zA-Z]+; Value 'your-agentcore-alias-id' at 'agentAliasId' failed to satisfy constraint: Member must have length less than or equal to 10
```

**Broken down:**

- An error occurred (ValidationException) when calling the InvokeAgent operation: 4 validation errors detected: Value '…
- Value 'your-agentcore-agent-id' at 'agentId' failed to satisfy constraint: Member must have length less than or equal…
- Value 'your-agentcore-alias-id' at 'agentAliasId' failed to satisfy constraint: Member must satisfy regular expressio…
- Value 'your-agentcore-alias-id' at 'agentAliasId' failed to satisfy constraint: Member must have length less than or …

### Claim 2: `CLM-C18214DFDB69`

**Drug:** Amlodipine 5mg (generic, tier 1, $9.25) · **Plan:** Commercial HMO Silver

**Result:** **Failed** — AgentCore IDs are placeholders

AWS expects short alphanumeric agent and alias IDs. Replace `AGENTCORE_AGENT_ID` and `AGENTCORE_AGENT_ALIAS_ID` with real values, or disable AgentCore in config so the demo does not call InvokeAgent.

**Raw message** (for support / search):

```text
An error occurred (ValidationException) when calling the InvokeAgent operation: 4 validation errors detected: Value 'your-agentcore-agent-id' at 'agentId' failed to satisfy constraint: Member must satisfy regular expression pattern: [0-9a-zA-Z]+; Value 'your-agentcore-agent-id' at 'agentId' failed to satisfy constraint: Member must have length less than or equal to 10; Value 'your-agentcore-alias-id' at 'agentAliasId' failed to satisfy constraint: Member must satisfy regular expression pattern: [0-9a-zA-Z]+; Value 'your-agentcore-alias-id' at 'agentAliasId' failed to satisfy constraint: Member must have length less than or equal to 10
```

**Broken down:**

- An error occurred (ValidationException) when calling the InvokeAgent operation: 4 validation errors detected: Value '…
- Value 'your-agentcore-agent-id' at 'agentId' failed to satisfy constraint: Member must have length less than or equal…
- Value 'your-agentcore-alias-id' at 'agentAliasId' failed to satisfy constraint: Member must satisfy regular expressio…
- Value 'your-agentcore-alias-id' at 'agentAliasId' failed to satisfy constraint: Member must have length less than or …

### Claim 3: `CLM-4100466362B8`

**Drug:** Lantus SoloStar 100u/mL (brand, tier 3, $312.00) · **Plan:** Self-Insured Employer Plan

**Result:** **Failed** — AgentCore IDs are placeholders

AWS expects short alphanumeric agent and alias IDs. Replace `AGENTCORE_AGENT_ID` and `AGENTCORE_AGENT_ALIAS_ID` with real values, or disable AgentCore in config so the demo does not call InvokeAgent.

**Raw message** (for support / search):

```text
An error occurred (ValidationException) when calling the InvokeAgent operation: 4 validation errors detected: Value 'your-agentcore-agent-id' at 'agentId' failed to satisfy constraint: Member must satisfy regular expression pattern: [0-9a-zA-Z]+; Value 'your-agentcore-agent-id' at 'agentId' failed to satisfy constraint: Member must have length less than or equal to 10; Value 'your-agentcore-alias-id' at 'agentAliasId' failed to satisfy constraint: Member must satisfy regular expression pattern: [0-9a-zA-Z]+; Value 'your-agentcore-alias-id' at 'agentAliasId' failed to satisfy constraint: Member must have length less than or equal to 10
```

**Broken down:**

- An error occurred (ValidationException) when calling the InvokeAgent operation: 4 validation errors detected: Value '…
- Value 'your-agentcore-agent-id' at 'agentId' failed to satisfy constraint: Member must have length less than or equal…
- Value 'your-agentcore-alias-id' at 'agentAliasId' failed to satisfy constraint: Member must satisfy regular expressio…
- Value 'your-agentcore-alias-id' at 'agentAliasId' failed to satisfy constraint: Member must have length less than or …

---

## Audit trail (this run)

The in-memory audit logger recorded **9** event(s). Types below are internal codes; the *Meaning* column is plain English.

| Time (UTC) | Event | Meaning | Claim | Result |
|------------|-------|---------|-------|--------|
| 2026-03-24 15:06:14 | `PII_SCRUB` | Member identifiers masked for safe processing. | `CLM-002D0007B9BA` | SUCCESS |
| 2026-03-24 15:06:14 | `GOVERNANCE_CHECK` | Policy allowed this action for this actor. | `CLM-002D0007B9BA` | ALLOW |
| 2026-03-24 15:06:14 | `ADJUDICATION_STARTED` | Workflow began adjudication for this claim. | `CLM-002D0007B9BA` | SUCCESS |
| 2026-03-24 15:06:14 | `PII_SCRUB` | Member identifiers masked for safe processing. | `CLM-C18214DFDB69` | SUCCESS |
| 2026-03-24 15:06:14 | `GOVERNANCE_CHECK` | Policy allowed this action for this actor. | `CLM-C18214DFDB69` | ALLOW |
| 2026-03-24 15:06:14 | `ADJUDICATION_STARTED` | Workflow began adjudication for this claim. | `CLM-C18214DFDB69` | SUCCESS |
| 2026-03-24 15:06:14 | `PII_SCRUB` | Member identifiers masked for safe processing. | `CLM-4100466362B8` | SUCCESS |
| 2026-03-24 15:06:14 | `GOVERNANCE_CHECK` | Policy allowed this action for this actor. | `CLM-4100466362B8` | ALLOW |
| 2026-03-24 15:06:14 | `ADJUDICATION_STARTED` | Workflow began adjudication for this claim. | `CLM-4100466362B8` | SUCCESS |

---

## Files from this run

- **`demo_output.json`** — Full JSON for this run (results + complete audit list):  
  `/Users/seyedeh.mirrahimi/Desktop/calclaim-demo/demo_output.json`

*End of report.*
