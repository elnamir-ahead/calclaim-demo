"""
Report builders for CalcClaim demo runs (no Rich dependency).

- **HTML** — default; self-contained file with typography and tables (open in a browser).
- **Markdown** — optional; use path ending in `.md`.

Optimized for human readers: plain-language summaries, short tables, and
explained audit rows — not only machine-oriented metrics.
"""

from __future__ import annotations

import html as html_module
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# --- Reference text (kept short on purpose) ---------------------------------

_SCENARIO_BLURB: dict[str, str] = {
    "mixed": "Typical mix of drugs, tiers, and plans (default demo).",
    "reversal": "First claim uses **reverse** action to exercise reversal / HITL paths.",
    "pa": "Dataset biased toward prior-authorization scenarios.",
    "dur": "Dataset biased toward drug–utilization / interaction scenarios.",
}

_AUDIT_EVENT_MEANING: dict[str, str] = {
    "PII_SCRUB": "Member identifiers masked for safe processing.",
    "GOVERNANCE_CHECK": "Policy allowed this action for this actor.",
    "GOVERNANCE_DENY": "Access or purpose check blocked the request.",
    "ADJUDICATION_STARTED": "Workflow began adjudication for this claim.",
    "AGENTCORE_INVOKED": "Amazon Bedrock AgentCore was called.",
    "MCP_TOOLS_INVOKED": "MCP server tool (e.g. formulary_tier_lookup) was called from LangGraph.",
    "AUDIT": "Generic audit record.",
}

_ENV_PURPOSE: dict[str, str] = {
    "AWS_REGION": "Default AWS region for SDK calls.",
    "BEDROCK_REGION": "Region for Bedrock Converse / guardrails.",
    "BEDROCK_GUARDRAIL_ID": "Bedrock Guardrail resource ID (empty = skip guardrail on Converse).",
    "BEDROCK_GUARDRAIL_VERSION": "Guardrail version (e.g. DRAFT or version number).",
    "AGENTCORE_AGENT_ID": "Bedrock AgentCore agent ID (short alphanumeric).",
    "AGENTCORE_AGENT_ALIAS_ID": "Agent alias ID for InvokeAgent.",
    "USE_AGENTCORE": "If true, graph may call AgentCore (needs real IDs).",
    "CALCLAIM_MCP_URL": "Streamable-HTTP MCP endpoint (e.g. http://127.0.0.1:8765/mcp) for formulary_tier_lookup in graph.",
    "USE_MCP_TOOLS": "If false, skip MCP tools node even when CALCLAIM_MCP_URL is set.",
    "REQUIRE_AUTH": "If true, FastAPI requires Bearer JWT (JWT_JWKS_URL); false when API Gateway validates JWT.",
    "TRUST_API_GATEWAY_AUTH": "If true, skip in-app JWT verify (edge already validated).",
    "JWT_JWKS_URL": "JWKS URL for RS256/ES256 JWT validation when REQUIRE_AUTH=true.",
    "USE_OPA": "If true and OPA_SERVER_URL set, policy_engine calls OPA with policies/calclaim.rego.",
    "LOG_FORMAT": "text or json — json adds correlation_id to log records.",
    "MCP_ALLOWED_HOSTS": "Comma-separated MCP hostnames; empty allows any (dev only).",
    "LANGCHAIN_TRACING_V2": "Send LangGraph/LangChain spans to LangSmith when true.",
    "DEMO_MODE": "Demo shortcuts (e.g. in-memory audit vs DynamoDB).",
}


def _format_run_time(iso_utc: str) -> str:
    """Turn ISO Z or offset string into a readable UTC line."""
    s = iso_utc.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return iso_utc


def _claim_context_line(claim: dict[str, Any]) -> str:
    drug = claim.get("drug") or {}
    plan = (claim.get("member") or {}).get("plan") or {}
    name = drug.get("name", "—")
    tier = drug.get("tier", "—")
    cost = drug.get("cost")
    cost_s = f"${float(cost):.2f}" if cost is not None else "—"
    plan_name = plan.get("name", "—")
    brand = "brand" if drug.get("brand") else "generic"
    return f"**Drug:** {name} ({brand}, tier {tier}, {cost_s}) · **Plan:** {plan_name}"


def _outcome_plain_english(status: str) -> str:
    return {
        "approved": "Approved — paid according to plan rules.",
        "approved_with_pa": "Approved with prior authorization noted.",
        "denied": "Blocked by governance / policy (not a pharmacy reject code).",
        "pending_review": "Held for manual or downstream review.",
        "unknown": "Workflow finished without a clear final status.",
        "rejected": "Rejected (benefit / edit code path).",
        "rejected_pending_pa": "Rejected pending prior authorization.",
        "rejected_refill_too_soon": "Rejected — refill too soon.",
        "rejected_dur": "Rejected — drug utilization review.",
        "reversed": "Reversal processed.",
    }.get(status, f"Status code: {status}")


def _e(text: str) -> str:
    """Escape for HTML body text."""
    return html_module.escape(text or "", quote=True)


def _env_row_cells(name: str) -> tuple[str, str, str]:
    """Variable name, plain value for display, purpose sentence."""
    v = os.getenv(name, "")
    if not v:
        return name, "(not set)", _ENV_PURPOSE.get(name, "See .env.example.")
    disp = v
    if len(disp) > 48:
        disp = f"{v[:24]}… (truncated)"
    return name, disp, _ENV_PURPOSE.get(name, "See .env.example.")


def _env_row(name: str) -> tuple[str, str, str]:
    """Markdown table row parts (value column may contain backticks)."""
    key, plain, purpose = _env_row_cells(name)
    display = "*(not set)*" if plain == "(not set)" else f"`{plain}`"
    return key, display, purpose


def _aggregate_run_stats(
    results: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
) -> dict[str, int]:
    ok = sum(1 for r in results if r.get("success"))
    failed = sum(1 for r in results if not r.get("success"))
    approved = sum(
        1
        for r in results
        if r.get("success")
        and r.get("result", {}).get("status") in ("approved", "approved_with_pa")
    )
    denied = sum(
        1
        for r in results
        if r.get("success") and r.get("result", {}).get("status") == "denied"
    )
    pending = sum(
        1
        for r in results
        if r.get("success")
        and r.get("result", {}).get("status") in ("pending_review", "unknown")
    )
    rejected_business = sum(
        1
        for r in results
        if r.get("success") and "rejected" in str(r.get("result", {}).get("status", ""))
    )
    return {
        "ok": ok,
        "failed": failed,
        "approved": approved,
        "denied": denied,
        "pending": pending,
        "rejected_business": rejected_business,
        "audit_count": len(audit_events),
        "n_results": len(results),
    }


def _summarize_error(message: str) -> tuple[str, str]:
    """
    Return (short headline for humans, body: prose or bullet list for details).
    """
    msg = (message or "").strip()
    if not msg:
        return "Unknown error", ""

    low = msg.lower()
    if "guardrail identifier is invalid" in low:
        return (
            "Bedrock rejected the Guardrail ID",
            "The value in `BEDROCK_GUARDRAIL_ID` is not a real guardrail in this "
            "account/region. Either remove it from `.env` or paste a valid ID from the "
            "Bedrock console → Guardrails.",
        )
    if "applyguardrail" in low and "incorrect format" in low:
        return (
            "Guardrail apply step failed",
            "Usually caused by a bad guardrail ID/version or payload shape. "
            "Clear `BEDROCK_GUARDRAIL_ID` to skip guardrails for local demos.",
        )
    if "invokeagent" in low and ("validation errors" in low or "agentid" in low):
        return (
            "AgentCore IDs are placeholders",
            "AWS expects short alphanumeric agent and alias IDs. Replace "
            "`AGENTCORE_AGENT_ID` and `AGENTCORE_AGENT_ALIAS_ID` with real values, "
            "or disable AgentCore in config so the demo does not call InvokeAgent.",
        )
    if "403" in msg and "langsmith" in low:
        return (
            "LangSmith returned HTTP 403",
            "Tracing may be off or the API key is invalid. Set "
            "`LANGCHAIN_TRACING_V2=false` or fix `LANGCHAIN_API_KEY`.",
        )

    # Extract ValidationException one-liner if present
    m = re.search(r"\(ValidationException\)\s*when calling the \w+ operation:\s*(.+)", msg)
    if m:
        tail = m.group(1).strip()
        if len(tail) > 200:
            tail = tail[:197] + "…"
        return ("AWS validation error", tail)

    if len(msg) > 280:
        return ("Error (see raw message below)", msg[:400] + ("…" if len(msg) > 400 else ""))
    return ("Error", msg)


def _split_validation_bullets(message: str) -> list[str]:
    """Turn long ValidationException text into shorter bullet lines."""
    parts = re.split(r";\s*(?=Value )", message)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if len(p) > 120:
            p = p[:117] + "…"
        if p:
            out.append(p)
    return out[:8] if out else [message[:200] + ("…" if len(message) > 200 else "")]


def build_markdown_report(
    *,
    run_at_utc: str,
    scenario: str,
    dataset_summary: dict[str, Any],
    results: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
    json_artifact: str,
    claims: Optional[list[dict[str, Any]]] = None,
) -> str:
    lines: list[str] = []
    when = _format_run_time(run_at_utc)

    lines.append("# CalcClaim demo report")
    lines.append("")
    lines.append(f"> **When:** {when}  ")
    lines.append(f"> **Scenario:** **{scenario}** — {_SCENARIO_BLURB.get(scenario, 'Custom scenario.')}")
    lines.append("")

    st = _aggregate_run_stats(results, audit_events)
    ok, failed = st["ok"], st["failed"]
    approved, denied = st["approved"], st["denied"]
    pending, rejected_business = st["pending"], st["rejected_business"]

    # --- At a glance (prose) ---
    lines.append("## At a glance")
    lines.append("")
    if failed == len(results) and len(results) > 0:
        lines.append(
            f"This run tried **{len(results)}** synthetic claim(s). **None finished successfully** — "
            f"each one raised an exception before a normal adjudication outcome was returned. "
            f"Scroll to **Per claim** for what broke and **Environment** for likely fixes."
        )
    elif failed > 0:
        lines.append(
            f"This run processed **{len(results)}** claim(s): **{ok}** completed without crashing "
            f"and **{failed}** failed with an exception. Successful outcomes: **{approved}** approved, "
            f"**{denied}** governance-denied, **{pending}** pending/unknown, **{rejected_business}** "
            f"with a reject-style status."
        )
    else:
        lines.append(
            f"This run processed **{len(results)}** claim(s); **all** finished without throwing. "
            f"Outcomes: **{approved}** approved, **{denied}** governance-denied, **{pending}** "
            f"pending/unknown, **{rejected_business}** reject-style."
        )
    lines.append("")
    if dataset_summary:
        liab = float(dataset_summary.get("total_plan_liability", 0) or 0)
        lines.append(
            f"Synthetic batch total plan liability (fake dollars): **${liab:,.2f}**."
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Quick stats table (plain labels) ---
    lines.append("## Quick stats")
    lines.append("")
    lines.append("| Question | Answer |")
    lines.append("|----------|--------|")
    lines.append(f"| How many claims? | {len(results)} |")
    lines.append(f"| Finished without an exception? | {ok} yes, {failed} no |")
    lines.append(f"| Approved (or approved with PA)? | {approved} |")
    lines.append(f"| Stopped by governance (`denied`)? | {denied} |")
    lines.append(f"| Needs review / unknown outcome? | {pending} |")
    lines.append(f"| Reject-style outcomes? | {rejected_business} |")
    lines.append(f"| Audit rows captured (demo memory)? | {st['audit_count']} |")
    lines.append("")
    lines.append(
        "*“Governance denied” means the workflow returned before a normal paid/reject decision, "
        "often due to policy or guardrail configuration.*"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- How to read ---
    lines.append("## How to read this report")
    lines.append("")
    lines.append("1. **Per claim** — One section per claim: what was simulated, then outcome or error.")
    lines.append("2. **Environment** — Shows non-secret settings; mismatches here usually explain errors.")
    lines.append("3. **Troubleshooting** — Common log messages and what to change in `.env` or AWS.")
    lines.append("4. **Audit trail** — Short table of what the demo recorded (PII scrub, governance, etc.).")
    lines.append("5. **`demo_output.json`** — Same run in JSON for tools and diffing.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Environment ---
    lines.append("## Environment (non-secret)")
    lines.append("")
    lines.append("| Variable | Value | Why it matters |")
    lines.append("|----------|-------|----------------|")
    for key in (
        "AWS_REGION",
        "BEDROCK_REGION",
        "BEDROCK_GUARDRAIL_ID",
        "BEDROCK_GUARDRAIL_VERSION",
        "AGENTCORE_AGENT_ID",
        "AGENTCORE_AGENT_ALIAS_ID",
        "USE_AGENTCORE",
        "CALCLAIM_MCP_URL",
        "USE_MCP_TOOLS",
        "REQUIRE_AUTH",
        "TRUST_API_GATEWAY_AUTH",
        "USE_OPA",
        "LOG_FORMAT",
        "MCP_ALLOWED_HOSTS",
        "LANGCHAIN_TRACING_V2",
        "DEMO_MODE",
    ):
        name, val, purpose = _env_row(key)
        lines.append(f"| `{name}` | {val} | {purpose} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Troubleshooting ---
    lines.append("## Troubleshooting cheat sheet")
    lines.append("")
    lines.append("#### Invalid Bedrock Guardrail ID")
    lines.append("")
    lines.append(
        "**Symptom:** `ValidationException` mentioning *guardrail identifier is invalid* on Converse. "
        "**Fix:** Remove `BEDROCK_GUARDRAIL_ID` from `.env` for local runs, or set a real ID from your account."
    )
    lines.append("")
    lines.append("#### Guardrail input format error")
    lines.append("")
    lines.append(
        "**Symptom:** `ApplyGuardrail` *incorrect format*. **Fix:** Same as above — bad ID/version often causes this."
    )
    lines.append("")
    lines.append("#### AgentCore / InvokeAgent validation")
    lines.append("")
    lines.append(
        "**Symptom:** Long message about `agentId` / `agentAliasId` pattern or length. "
        "**Fix:** Use real AgentCore IDs from the console, or turn off AgentCore so the graph skips that node."
    )
    lines.append("")
    lines.append("#### LangSmith HTTP 403")
    lines.append("")
    lines.append(
        "**Symptom:** Error posting to `api.smith.langchain.com`. **Fix:** Disable tracing "
        "(`LANGCHAIN_TRACING_V2=false`) or supply a valid `LANGCHAIN_API_KEY`."
    )
    lines.append("")
    lines.append("#### Very few workflow steps, outcome `denied`")
    lines.append("")
    lines.append(
        "**Symptom:** ~3 audit events and immediate `denied`. **Fix:** Often PHI purpose mapping — "
        "ensure `adjudicate` maps to an allowed purpose in policy code."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Per claim ---
    lines.append("## Per claim")
    lines.append("")
    claim_by_id: dict[str, dict[str, Any]] = {}
    if claims:
        for c in claims:
            cid = c.get("claim_id")
            if cid:
                claim_by_id[str(cid)] = c

    for i, row in enumerate(results, 1):
        cid = str(row.get("claim_id", "?"))
        lines.append(f"### Claim {i}: `{cid}`")
        lines.append("")
        claim = claim_by_id.get(cid)
        if claim:
            lines.append(_claim_context_line(claim))
            lines.append("")

        if not row.get("success"):
            raw = row.get("error") or ""
            headline, detail = _summarize_error(raw)
            lines.append(f"**Result:** **Failed** — {headline}")
            lines.append("")
            if detail and detail != raw:
                lines.append(detail)
                lines.append("")
            lines.append("**Raw message** (for support / search):")
            lines.append("")
            lines.append("```text")
            lines.append(raw.strip()[:6000] if len(raw) > 6000 else raw.strip())
            lines.append("```")
            lines.append("")
            if "validation errors" in raw.lower() or "Value '" in raw:
                bullets = _split_validation_bullets(raw)
                if len(bullets) > 1:
                    lines.append("**Broken down:**")
                    lines.append("")
                    for b in bullets:
                        lines.append(f"- {b}")
                    lines.append("")
            continue

        res = row.get("result") or {}
        st = str(res.get("status", "unknown"))
        lines.append(f"**Result:** **{_outcome_plain_english(st)}** (`{st}`)")
        lines.append("")
        pricing = res.get("pricing") or {}
        lines.append(
            f"- **Member cost (copay):** ${float(pricing.get('copay', 0) or 0):.2f}  \n"
            f"- **Plan pays:** ${float(pricing.get('plan_pay', 0) or 0):.2f}"
        )
        steps = res.get("workflow_steps") or []
        if steps:
            lines.append(f"- **Steps executed:** {' → '.join(steps)}")
        else:
            lines.append("- **Steps executed:** *(none listed — often an early exit)*")
        lines.append(
            f"- **Audit entries attached to this response:** {len(res.get('audit_trail', []))}"
        )
        if res.get("reject_code"):
            lines.append(f"- **Reject code:** {res.get('reject_code')}")
        if res.get("hitl_resolution"):
            lines.append(f"- **HITL:** {res.get('hitl_resolution')}")
        if res.get("status") == "denied":
            lines.append(f"- **Policy:** {res.get('policy_id') or '—'}")
            lines.append(f"- **Reason:** {res.get('reason') or '—'}")
        if res.get("agentcore_used") is not None:
            ac = "yes" if res.get("agentcore_used") else "no"
            ms = res.get("agentcore_ms")
            lines.append(
                f"- **AgentCore used:** {ac}"
                + (f" ({ms} ms)" if ms is not None else "")
            )
        lines.append("")

    lines.append("---")
    lines.append("")

    # --- Audit ---
    lines.append("## Audit trail (this run)")
    lines.append("")
    lines.append(
        f"The in-memory audit logger recorded **{len(audit_events)}** event(s). "
        f"Types below are internal codes; the *Meaning* column is plain English."
    )
    lines.append("")
    if audit_events:
        lines.append("| Time (UTC) | Event | Meaning | Claim | Result |")
        lines.append("|------------|-------|---------|-------|--------|")
        for ev in audit_events[-30:]:
            et = ev.get("event_type", "")
            meaning = _AUDIT_EVENT_MEANING.get(et, "See governance/audit code.")
            ts = (ev.get("timestamp_utc") or "")[:19].replace("T", " ")
            lines.append(
                "| {ts} | `{et}` | {m} | `{cid}` | {out} |".format(
                    ts=ts or "—",
                    et=et,
                    m=meaning,
                    cid=ev.get("claim_id", "—"),
                    out=ev.get("outcome", "—"),
                )
            )
        if len(audit_events) > 30:
            lines.append("")
            lines.append(f"*Table shows the last **30** of **{len(audit_events)}** events.*")
    else:
        lines.append("*No audit events were recorded.*")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Artifacts ---
    lines.append("## Files from this run")
    lines.append("")
    lines.append(
        f"- **`demo_output.json`** — Full JSON for this run (results + complete audit list):  \n"
        f"  `{json_artifact}`"
    )
    lines.append("")
    lines.append("*End of report.*")
    lines.append("")
    return "\n".join(lines)


def default_report_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    root = Path(__file__).resolve().parent.parent
    out_dir = root / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"calclaim-run-{ts}.html"


def _claim_context_plain(claim: dict[str, Any]) -> str:
    """Single line for HTML (no markdown bold)."""
    drug = claim.get("drug") or {}
    plan = (claim.get("member") or {}).get("plan") or {}
    name = drug.get("name", "—")
    tier = drug.get("tier", "—")
    cost = drug.get("cost")
    cost_s = f"${float(cost):.2f}" if cost is not None else "—"
    plan_name = plan.get("name", "—")
    brand = "brand" if drug.get("brand") else "generic"
    return f"Drug: {name} ({brand}, tier {tier}, {cost_s}) · Plan: {plan_name}"


_HTML_STYLES = """
:root {
  --bg: #f8fafc;
  --card: #ffffff;
  --text: #0f172a;
  --muted: #64748b;
  --border: #e2e8f0;
  --accent: #1d4ed8;
  --bad: #b91c1c;
  --good: #15803d;
}
* { box-sizing: border-box; }
body {
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  line-height: 1.55;
  color: var(--text);
  background: var(--bg);
  margin: 0;
  padding: 1.5rem 1.25rem 3rem;
}
.wrap { max-width: 52rem; margin: 0 auto; }
h1 { font-size: 1.65rem; font-weight: 700; margin: 0 0 0.5rem; letter-spacing: -0.02em; }
h2 { font-size: 1.2rem; font-weight: 650; margin: 2rem 0 0.75rem; padding-bottom: 0.35rem; border-bottom: 2px solid var(--border); }
h3 { font-size: 1.05rem; font-weight: 650; margin: 1.25rem 0 0.5rem; }
h4 { font-size: 0.95rem; font-weight: 650; margin: 1rem 0 0.35rem; color: #334155; }
.meta {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem 1.15rem;
  margin-bottom: 1.5rem;
  box-shadow: 0 1px 2px rgba(15,23,42,0.04);
}
.meta p { margin: 0.35rem 0; color: var(--muted); font-size: 0.95rem; }
.meta strong { color: var(--text); }
.lead { font-size: 1.02rem; color: #334155; margin: 0.75rem 0 1rem; line-height: 1.6; }
.note { font-size: 0.9rem; color: var(--muted); font-style: italic; margin-top: 0.5rem; }
ol.howto { margin: 0.5rem 0; padding-left: 1.35rem; color: #334155; }
ol.howto li { margin: 0.35rem 0; }
table.data { width: 100%; border-collapse: collapse; font-size: 0.9rem; background: var(--card); border-radius: 8px; overflow: hidden; border: 1px solid var(--border); }
table.data th, table.data td { padding: 0.55rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border); vertical-align: top; }
table.data th { background: #f1f5f9; font-weight: 600; color: #334155; }
table.data tr:last-child td { border-bottom: none; }
table.data code { font-size: 0.82rem; }
code { background: #f1f5f9; padding: 0.12em 0.4em; border-radius: 4px; font-size: 0.88em; }
pre.raw {
  background: #0f172a;
  color: #e2e8f0;
  padding: 1rem;
  border-radius: 8px;
  overflow-x: auto;
  font-size: 0.78rem;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem 1.1rem;
  margin-bottom: 1rem;
  box-shadow: 0 1px 2px rgba(15,23,42,0.04);
}
.card.fail { border-left: 4px solid var(--bad); }
.card.ok { border-left: 4px solid var(--good); }
.claim-ctx { font-size: 0.92rem; color: #475569; margin-bottom: 0.75rem; }
.symptom { margin: 0.4rem 0 0.6rem; color: #334155; }
ul.bullets { margin: 0.4rem 0; padding-left: 1.2rem; }
ul.bullets li { margin: 0.25rem 0; }
footer.files { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--border); font-size: 0.9rem; color: var(--muted); }
@media print {
  body { background: #fff; }
  .card { break-inside: avoid; }
}
"""


def build_html_report(
    *,
    run_at_utc: str,
    scenario: str,
    dataset_summary: dict[str, Any],
    results: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
    json_artifact: str,
    claims: Optional[list[dict[str, Any]]] = None,
) -> str:
    when = _format_run_time(run_at_utc)
    st = _aggregate_run_stats(results, audit_events)
    ok, failed = st["ok"], st["failed"]
    approved, denied = st["approved"], st["denied"]
    pending, rb = st["pending"], st["rejected_business"]
    n = st["n_results"]

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(f"<title>{_e('CalcClaim demo report')}</title>")
    parts.append(f"<style>{_HTML_STYLES}</style>")
    parts.append("</head><body><div class=\"wrap\">")

    parts.append("<h1>CalcClaim demo report</h1>")
    parts.append('<div class="meta">')
    parts.append(f"<p><strong>When:</strong> {_e(when)}</p>")
    blurb = _SCENARIO_BLURB.get(scenario, "Custom scenario.")
    parts.append(
        f"<p><strong>Scenario:</strong> {_e(scenario)} — {_e(blurb)}</p>"
    )
    parts.append("</div>")

    parts.append("<h2>At a glance</h2>")
    if failed == n and n > 0:
        lead = (
            f"This run tried <strong>{n}</strong> synthetic claim(s). "
            "<strong>None finished successfully</strong> — each one raised an exception before "
            "a normal adjudication outcome was returned. See <strong>Per claim</strong> below and "
            "<strong>Environment</strong> for likely fixes."
        )
    elif failed > 0:
        lead = (
            f"This run processed <strong>{n}</strong> claim(s): <strong>{ok}</strong> completed "
            f"without crashing and <strong>{failed}</strong> failed with an exception. "
            f"Successful outcomes: <strong>{approved}</strong> approved, <strong>{denied}</strong> "
            f"governance-denied, <strong>{pending}</strong> pending/unknown, <strong>{rb}</strong> "
            "reject-style."
        )
    else:
        lead = (
            f"This run processed <strong>{n}</strong> claim(s); <strong>all</strong> finished without throwing. "
            f"Outcomes: <strong>{approved}</strong> approved, <strong>{denied}</strong> governance-denied, "
            f"<strong>{pending}</strong> pending/unknown, <strong>{rb}</strong> reject-style."
        )
    parts.append(f'<p class="lead">{lead}</p>')
    if dataset_summary:
        liab = float(dataset_summary.get("total_plan_liability", 0) or 0)
        parts.append(
            f'<p class="lead">Synthetic batch total plan liability (fake dollars): '
            f"<strong>${liab:,.2f}</strong>.</p>"
        )

    parts.append("<h2>Quick stats</h2>")
    parts.append('<table class="data">')
    parts.append("<thead><tr><th>Question</th><th>Answer</th></tr></thead><tbody>")
    rows = [
        ("How many claims?", str(n)),
        ("Finished without an exception?", f"{ok} yes, {failed} no"),
        ("Approved (or approved with PA)?", str(approved)),
        ("Stopped by governance (denied)?", str(denied)),
        ("Needs review / unknown outcome?", str(pending)),
        ("Reject-style outcomes?", str(rb)),
        ("Audit rows captured (demo memory)?", str(st["audit_count"])),
    ]
    for q, a in rows:
        parts.append(f"<tr><td>{_e(q)}</td><td>{_e(a)}</td></tr>")
    parts.append("</tbody></table>")
    parts.append(
        '<p class="note">“Governance denied” means the workflow returned before a normal paid/reject '
        "decision, often due to policy or guardrail configuration.</p>"
    )

    parts.append("<h2>How to read this report</h2>")
    parts.append('<ol class="howto">')
    for item in (
        "<strong>Per claim</strong> — One section per claim: what was simulated, then outcome or error.",
        "<strong>Environment</strong> — Non-secret settings; mismatches here usually explain errors.",
        "<strong>Troubleshooting</strong> — Common messages and what to change in .env or AWS.",
        "<strong>Audit trail</strong> — What the demo recorded (PII scrub, governance, etc.).",
        "<strong>demo_output.json</strong> — Same run in JSON for tools and diffing.",
    ):
        parts.append(f"<li>{item}</li>")
    parts.append("</ol>")

    parts.append("<h2>Environment (non-secret)</h2>")
    parts.append('<table class="data">')
    parts.append("<thead><tr><th>Variable</th><th>Value</th><th>Why it matters</th></tr></thead><tbody>")
    env_keys = (
        "AWS_REGION",
        "BEDROCK_REGION",
        "BEDROCK_GUARDRAIL_ID",
        "BEDROCK_GUARDRAIL_VERSION",
        "AGENTCORE_AGENT_ID",
        "AGENTCORE_AGENT_ALIAS_ID",
        "USE_AGENTCORE",
        "CALCLAIM_MCP_URL",
        "USE_MCP_TOOLS",
        "REQUIRE_AUTH",
        "TRUST_API_GATEWAY_AUTH",
        "USE_OPA",
        "LOG_FORMAT",
        "MCP_ALLOWED_HOSTS",
        "LANGCHAIN_TRACING_V2",
        "DEMO_MODE",
    )
    for key in env_keys:
        _, val, purpose = _env_row_cells(key)
        parts.append(
            "<tr><td><code>{}</code></td><td>{}</td><td>{}</td></tr>".format(
                _e(key), _e(val), _e(purpose)
            )
        )
    parts.append("</tbody></table>")

    parts.append("<h2>Troubleshooting cheat sheet</h2>")
    tips = [
        (
            "Invalid Bedrock Guardrail ID",
            "ValidationException mentioning guardrail identifier is invalid on Converse. "
            "Remove BEDROCK_GUARDRAIL_ID from .env for local runs, or set a real ID from your account.",
        ),
        (
            "Guardrail input format error",
            "ApplyGuardrail incorrect format. Same as above — bad ID/version often causes this.",
        ),
        (
            "AgentCore / InvokeAgent validation",
            "Long message about agentId / agentAliasId pattern or length. "
            "Use real AgentCore IDs from the console, or turn off AgentCore so the graph skips that node.",
        ),
        (
            "LangSmith HTTP 403",
            "Error posting to api.smith.langchain.com. Disable tracing (LANGCHAIN_TRACING_V2=false) "
            "or supply a valid LANGCHAIN_API_KEY.",
        ),
        (
            "Very few workflow steps, outcome denied",
            "~3 audit events and immediate denied. Often PHI purpose mapping — ensure adjudicate maps "
            "to an allowed purpose in policy code.",
        ),
    ]
    for title, body in tips:
        parts.append(f"<h4>{_e(title)}</h4>")
        parts.append(f'<p class="symptom">{_e(body)}</p>')

    parts.append("<h2>Per claim</h2>")
    claim_by_id: dict[str, dict[str, Any]] = {}
    if claims:
        for c in claims:
            cid = c.get("claim_id")
            if cid:
                claim_by_id[str(cid)] = c

    for i, row in enumerate(results, 1):
        cid = str(row.get("claim_id", "?"))
        success = bool(row.get("success"))
        card_cls = "card ok" if success else "card fail"
        parts.append(f'<section class="{card_cls}">')
        parts.append(f"<h3>Claim {i}: <code>{_e(cid)}</code></h3>")
        claim = claim_by_id.get(cid)
        if claim:
            parts.append(f'<p class="claim-ctx">{_e(_claim_context_plain(claim))}</p>')

        if not success:
            raw = row.get("error") or ""
            headline, detail = _summarize_error(raw)
            parts.append(f"<p><strong>Result:</strong> <strong>Failed</strong> — {_e(headline)}</p>")
            if detail and detail != raw:
                parts.append(f"<p>{_e(detail)}</p>")
            parts.append("<p><strong>Raw message</strong> (for support / search):</p>")
            raw_trim = raw.strip()[:6000] if len(raw) > 6000 else raw.strip()
            parts.append(f"<pre class=\"raw\">{_e(raw_trim)}</pre>")
            if "validation errors" in raw.lower() or "Value '" in raw:
                bullets = _split_validation_bullets(raw)
                if len(bullets) > 1:
                    parts.append("<p><strong>Broken down:</strong></p><ul class=\"bullets\">")
                    for b in bullets:
                        parts.append(f"<li>{_e(b)}</li>")
                    parts.append("</ul>")
            parts.append("</section>")
            continue

        res = row.get("result") or {}
        stt = str(res.get("status", "unknown"))
        parts.append(
            f"<p><strong>Result:</strong> {_e(_outcome_plain_english(stt))} "
            f"(<code>{_e(stt)}</code>)</p>"
        )
        pricing = res.get("pricing") or {}
        parts.append(
            "<ul class=\"bullets\">"
            f"<li>Member cost (copay): ${float(pricing.get('copay', 0) or 0):.2f}</li>"
            f"<li>Plan pays: ${float(pricing.get('plan_pay', 0) or 0):.2f}</li>"
        )
        steps = res.get("workflow_steps") or []
        if steps:
            parts.append(f"<li>Steps executed: {_e(' → '.join(steps))}</li>")
        else:
            parts.append("<li>Steps executed: <em>none listed — often an early exit</em></li>")
        parts.append(
            f"<li>Audit entries attached to this response: {len(res.get('audit_trail', []))}</li>"
        )
        if res.get("reject_code"):
            parts.append(f"<li>Reject code: {_e(str(res.get('reject_code')))}</li>")
        if res.get("hitl_resolution"):
            parts.append(f"<li>HITL: {_e(str(res.get('hitl_resolution')))}</li>")
        if res.get("status") == "denied":
            parts.append(
                f"<li>Policy: {_e(str(res.get('policy_id') or '—'))}</li>"
                f"<li>Reason: {_e(str(res.get('reason') or '—'))}</li>"
            )
        if res.get("agentcore_used") is not None:
            ac = "yes" if res.get("agentcore_used") else "no"
            ms = res.get("agentcore_ms")
            extra = f" ({ms} ms)" if ms is not None else ""
            parts.append(f"<li>AgentCore used: {ac}{_e(extra)}</li>")
        parts.append("</ul></section>")

    parts.append("<h2>Audit trail (this run)</h2>")
    parts.append(
        f"<p>The in-memory audit logger recorded <strong>{len(audit_events)}</strong> event(s). "
        "The <em>Meaning</em> column is plain English.</p>"
    )
    if audit_events:
        parts.append('<table class="data">')
        parts.append(
            "<thead><tr><th>Time (UTC)</th><th>Event</th><th>Meaning</th>"
            "<th>Claim</th><th>Result</th></tr></thead><tbody>"
        )
        for ev in audit_events[-30:]:
            et = ev.get("event_type", "")
            meaning = _AUDIT_EVENT_MEANING.get(et, "See governance/audit code.")
            ts = (ev.get("timestamp_utc") or "")[:19].replace("T", " ")
            parts.append(
                "<tr><td>{}</td><td><code>{}</code></td><td>{}</td><td><code>{}</code></td><td>{}</td></tr>".format(
                    _e(ts or "—"),
                    _e(et),
                    _e(meaning),
                    _e(str(ev.get("claim_id", "—"))),
                    _e(str(ev.get("outcome", "—"))),
                )
            )
        parts.append("</tbody></table>")
        if len(audit_events) > 30:
            parts.append(
                f"<p class=\"note\">Table shows the last <strong>30</strong> of "
                f"<strong>{len(audit_events)}</strong> events.</p>"
            )
    else:
        parts.append("<p><em>No audit events were recorded.</em></p>")

    parts.append('<footer class="files">')
    parts.append("<strong>demo_output.json</strong> — full JSON for this run:<br>")
    parts.append(f'<code style="word-break:break-all;">{_e(json_artifact)}</code>')
    parts.append("<p>End of report.</p></footer>")

    parts.append("</div></body></html>")
    return "\n".join(parts)
