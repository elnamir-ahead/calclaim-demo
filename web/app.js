/**
 * CalcClaim demo portal — calls POST /claims/adjudicate on the same API.
 */
(function () {
  "use strict";

  const STORAGE_KEY = "calclaim_api_base";

  const DRUGS = [
    { ndc: "00093-0058-01", name: "Lisinopril 10mg", tier: 1, cost: 12.5 },
    { ndc: "68180-0513-01", name: "Atorvastatin 20mg", tier: 2, cost: 18.75 },
    { ndc: "00071-0155-23", name: "Lipitor 20mg", tier: 3, cost: 145, brand: true },
    { ndc: "00088-2218-47", name: "Lantus SoloStar", tier: 3, cost: 312, brand: true },
    { ndc: "00006-0021-31", name: "Keytruda 100mg/4mL", tier: 5, cost: 12500, requires_pa: true },
  ];

  const PLANS = [
    { plan_id: "PLN-COMM-001", name: "Commercial PPO Gold" },
    { plan_id: "PLN-MEDV-001", name: "Medicare Part D PDP" },
    { plan_id: "PLN-SELF-001", name: "Self-Insured Employer" },
  ];

  function apiBase() {
    const v = localStorage.getItem(STORAGE_KEY);
    return (v || "").replace(/\/$/, "");
  }

  function setStatus(msg, show) {
    const el = document.getElementById("status");
    el.textContent = msg || "";
    el.classList.toggle("hidden", !show);
  }

  function setError(msg) {
    const el = document.getElementById("error");
    el.textContent = msg || "";
    el.classList.toggle("hidden", !msg);
  }

  function buildGuidedClaim() {
    const drugSel = document.getElementById("guided-drug");
    const planSel = document.getElementById("guided-plan");
    const scenario = document.getElementById("guided-scenario").value;
    const drug = DRUGS[Number(drugSel.value)];
    const plan = PLANS[Number(planSel.value)];

    const tierCopays = { 1: 10, 2: 35, 3: 70, 4: 100, 5: 200 };
    const tier = drug.tier;
    const copay = tierCopays[tier] || 35;
    const plan_pay = Math.max(0, drug.cost - copay);

    let status = "approved";
    let reject_code = null;
    let reject_message = null;
    let dur_alerts = [];

    if (scenario === "prior_auth" || drug.requires_pa) {
      status = "rejected_pending_pa";
      reject_code = "75";
      reject_message = "Prior authorization required";
    } else if (scenario === "dur") {
      status = "rejected_dur";
      reject_code = "88";
      reject_message = "DUR reject — drug interaction";
      dur_alerts = [
        {
          alert_type: "DD",
          description: "Potential interaction on profile (demo)",
          severity: "moderate",
        },
      ];
    }

    return {
      claim_id: "CLM-GUIDED-" + Math.random().toString(36).slice(2, 10).toUpperCase(),
      transaction_id: "TXN-GUIDED-" + Math.random().toString(36).slice(2, 12).toUpperCase(),
      submitted_at: new Date().toISOString(),
      status,
      member: {
        member_id: "MBR-GUIDED-001",
        first_name: "Demo",
        last_name: "Member",
        plan: { plan_id: plan.plan_id, name: plan.name },
      },
      drug: {
        ndc: drug.ndc,
        name: drug.name,
        tier: drug.tier,
        gpi: "00000000000000",
      },
      pricing: {
        ingredient_cost: drug.cost,
        dispensing_fee: 2.5,
        copay: status === "approved" ? copay : 0,
        plan_pay: status === "approved" ? Math.round(plan_pay * 100) / 100 : 0,
        total_submitted: drug.cost + 2.5,
      },
      reject_code,
      reject_message,
      prior_auth: {
        required: !!drug.requires_pa || scenario === "prior_auth",
        number: null,
      },
      dur_alerts,
    };
  }

  /** LangGraph workflow step id → label (matches claims_workflow.py). */
  const CC2_STAGES = [
    { step: "calc_claim2_cost_core", label: "CostCore", ctxKey: "cost" },
    { step: "calc_claim2_copay", label: "Copay", ctxKey: "copay" },
    { step: "calc_claim2_medicare_d", label: "Medicare D", ctxKey: "medicare_d" },
    { step: "calc_claim2_margin", label: "Margin", ctxKey: "margin" },
    { step: "calc_claim2_deductible_cap", label: "Ded / caps", ctxKey: "deductible_cap" },
    { step: "calc_claim2_special", label: "Special", ctxKey: "special" },
    { step: "calc_claim2_orchestrator", label: "Orchestrator", ctxKey: "orchestrator" },
  ];

  function renderCalcClaim2Card(workflowSteps, cc2) {
    const steps = workflowSteps || [];
    const ranAny = CC2_STAGES.some((s) => steps.includes(s.step));
    const items = CC2_STAGES.map((s) => {
      const done = steps.includes(s.step);
      const cls = done ? "done" : "skipped";
      const mark = done ? "✓" : "○";
      return `<li class="${cls}" title="${escapeHtml(s.step)}"><span aria-hidden="true">${mark}</span> ${escapeHtml(s.label)}</li>`;
    }).join("");

    const rc =
      cc2 && typeof cc2.return_code === "number"
        ? cc2.return_code
        : cc2 && cc2.orchestrator && typeof cc2.orchestrator.return_code === "number"
          ? cc2.orchestrator.return_code
          : null;
    let rcClass = "cc2-rc-0";
    if (rc === 3) rcClass = "cc2-rc-3";
    if (rc === 6) rcClass = "cc2-rc-6";

    const validated =
      cc2 && cc2.orchestrator && typeof cc2.orchestrator.validated === "boolean"
        ? cc2.orchestrator.validated
        : null;
    const patient = cc2 && cc2.orchestrator ? cc2.orchestrator.patient_pay_demo : null;
    const plan = cc2 && cc2.orchestrator ? cc2.orchestrator.plan_pay_demo : null;

    let body;
    if (!ranAny && !cc2) {
      body = `<p class="muted">This run did not execute the <strong>claims</strong> calcClaim2 pipeline (e.g. supervisor routed to formulary/compliance, or governance denied before agents).</p>`;
    } else {
      body = `
        <div class="cc2-live-pipeline">
          <p class="muted" style="margin:0 0 0.5rem;font-size:0.8rem;">Stages executed (matches PDF modular flow + pytest components)</p>
          <ul class="cc2-live-steps">${items}</ul>
        </div>
        ${
          cc2
            ? `<dl class="kv-grid" style="margin-top:1rem;">
          <div><dt>Return code</dt><dd><span class="cc2-rc-badge ${rcClass}">${rc !== null ? escapeHtml(String(rc)) : "—"}</span><span class="muted" style="font-size:0.75rem;">0 ok · 3 MBA limit · 6 error</span></dd></div>
          <div><dt>validateResults</dt><dd>${validated === null ? "—" : validated ? "Passed" : "Failed"}</dd></div>
          <div><dt>Patient pay (demo)</dt><dd>${patient != null ? "$" + num(patient) : "—"}</dd></div>
          <div><dt>Plan pay (demo)</dt><dd>${plan != null ? "$" + num(plan) : "—"}</dd></div>
        </dl>
        <details class="cc2-json-details">
          <summary>Show full <code>calc_claim2</code> context JSON</summary>
          <pre class="cc2-json-pre">${escapeHtml(JSON.stringify(cc2, null, 2))}</pre>
        </details>`
            : `<p class="muted" style="margin-top:0.75rem;">No <code>calc_claim2</code> object on response (older API build).</p>`
        }
      `;
    }

    return `
      <div class="summary-card">
        <h3>calcClaim2 pipeline (this run)</h3>
        ${body}
      </div>
    `;
  }

  const SAMPLE_JSON = `{
  "claim_id": "CLM-DEMO-JSON-001",
  "status": "approved",
  "member": {
    "member_id": "MBR-JSON-001",
    "first_name": "Sample",
    "last_name": "Claim",
    "plan": { "plan_id": "PLN-COMM-001", "name": "Commercial PPO Gold" }
  },
  "drug": {
    "ndc": "00085-0221-01",
    "name": "Metformin 500mg",
    "tier": 1,
    "gpi": "27600030100310"
  },
  "pricing": {
    "ingredient_cost": 8.0,
    "dispensing_fee": 2.5,
    "copay": 10.0,
    "plan_pay": 0.5,
    "total_submitted": 10.5
  },
  "prior_auth": { "required": false },
  "dur_alerts": []
}`;

  function renderResult(apiJson) {
    const section = document.getElementById("result-section");
    const summary = document.getElementById("result-summary");
    const raw = document.getElementById("result-raw");

    const result = apiJson.result || {};
    raw.textContent = JSON.stringify(apiJson, null, 2);

    const status = (result.status || "unknown").toLowerCase();
    let pillClass = "pending";
    if (status.includes("approv")) pillClass = "approved";
    if (status.includes("reject") || status.includes("denied")) pillClass = "rejected";

    const pricing = result.pricing || {};
    const steps = result.workflow_steps || [];
    const dur = result.dur_alerts || [];
    const cc2 = result.calc_claim2;

    summary.innerHTML = `
      <div class="summary-card">
        <h3>Outcome</h3>
        <p><span class="status-pill ${pillClass}">${escapeHtml(result.status || "—")}</span></p>
        <dl class="kv-grid">
          <div><dt>Claim ID</dt><dd>${escapeHtml(String(result.claim_id || "—"))}</dd></div>
          <div><dt>Session</dt><dd>${escapeHtml(String(apiJson.session_id || "—"))}</dd></div>
          <div><dt>Copay</dt><dd>$${num(pricing.copay)}</dd></div>
          <div><dt>Plan pay</dt><dd>$${num(pricing.plan_pay)}</dd></div>
          <div><dt>Confidence</dt><dd>${result.confidence != null ? escapeHtml(String(result.confidence)) : "—"}</dd></div>
          <div><dt>Guardrail</dt><dd>${result.guardrail_intervened ? "Intervened" : "Clear"}</dd></div>
        </dl>
      </div>
      <div class="summary-card">
        <h3>Codes &amp; reasons</h3>
        <dl class="kv-grid">
          <div><dt>Reject code</dt><dd>${escapeHtml(String(result.reject_code ?? "—"))}</dd></div>
          <div><dt>Reason</dt><dd>${escapeHtml(String(result.reject_reason ?? "—"))}</dd></div>
          <div><dt>HITL</dt><dd>${escapeHtml(String(result.hitl_resolution ?? "—"))}</dd></div>
        </dl>
      </div>
      <div class="summary-card">
        <h3>DUR / clinical alerts</h3>
        ${
          dur.length
            ? `<ul class="steps-list">${dur.map((d) => `<li>${escapeHtml(JSON.stringify(d))}</li>`).join("")}</ul>`
            : "<p class=\"muted\">None</p>"
        }
      </div>
      ${renderCalcClaim2Card(steps, cc2)}
      <div class="summary-card">
        <h3>Workflow steps (audit trail)</h3>
        ${
          steps.length
            ? `<ol class="steps-list">${steps.map((s) => `<li>${escapeHtml(String(s))}</li>`).join("")}</ol>`
            : "<p>—</p>"
        }
        <p style="font-size:0.8rem;color:var(--muted);margin:0.5rem 0 0;">Audit event IDs: ${(result.audit_trail || []).length}</p>
      </div>
    `;

    section.classList.remove("hidden");
    section.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function num(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(2) : "—";
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  async function postAdjudicate(body) {
    const base = apiBase();
    const url = `${base}/claims/adjudicate`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(text.slice(0, 200) || `HTTP ${res.status}`);
    }
    if (!res.ok) {
      const detail = data.detail || data.message || JSON.stringify(data);
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function initTabs() {
    const tabs = document.querySelectorAll(".tab");
    const panels = document.querySelectorAll(".tab-panel");
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const id = tab.getAttribute("aria-controls");
        tabs.forEach((t) => {
          const on = t === tab;
          t.classList.toggle("active", on);
          t.setAttribute("aria-selected", on ? "true" : "false");
        });
        panels.forEach((p) => {
          const show = p.id === id;
          p.classList.toggle("hidden", !show);
          p.hidden = !show;
        });
      });
    });
  }

  function initGuided() {
    const drugSel = document.getElementById("guided-drug");
    const planSel = document.getElementById("guided-plan");
    DRUGS.forEach((d, i) => {
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = `${d.name} (tier ${d.tier})`;
      drugSel.appendChild(o);
    });
    PLANS.forEach((p, i) => {
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = `${p.name}`;
      planSel.appendChild(o);
    });
  }

  function initSettings() {
    const input = document.getElementById("api-base");
    input.value = localStorage.getItem(STORAGE_KEY) || "";
    document.getElementById("btn-settings").addEventListener("click", () => {
      const p = document.getElementById("panel-settings");
      const btn = document.getElementById("btn-settings");
      const open = p.classList.toggle("hidden");
      btn.setAttribute("aria-expanded", open ? "false" : "true");
    });
    document.getElementById("btn-save-settings").addEventListener("click", () => {
      localStorage.setItem(STORAGE_KEY, input.value.trim());
      setStatus("API base saved.", true);
      setTimeout(() => setStatus("", false), 2500);
    });
  }

  document.getElementById("btn-quick").addEventListener("click", async () => {
    setError("");
    setStatus("Running LangGraph adjudication…", true);
    try {
      const data = await postAdjudicate({
        use_demo_claim: true,
        actor_id: "web-demo-quick",
        actor_role: "claims_processor",
        action: "adjudicate",
      });
      setStatus("", false);
      renderResult(data);
    } catch (e) {
      setStatus("", false);
      setError(e.message || String(e));
    }
  });

  document.getElementById("btn-guided").addEventListener("click", async () => {
    setError("");
    const actor = document.getElementById("guided-actor").value.trim() || "portal-user-001";
    const claim = buildGuidedClaim();
    setStatus("Submitting guided claim…", true);
    try {
      const data = await postAdjudicate({
        claim,
        actor_id: actor,
        actor_role: "claims_processor",
        action: "adjudicate",
      });
      setStatus("", false);
      renderResult(data);
    } catch (e) {
      setStatus("", false);
      setError(e.message || String(e));
    }
  });

  document.getElementById("btn-json").addEventListener("click", async () => {
    setError("");
    let claim;
    try {
      claim = JSON.parse(document.getElementById("claim-json").value);
    } catch (e) {
      setError("Invalid JSON: " + e.message);
      return;
    }
    setStatus("Submitting claim JSON…", true);
    try {
      const data = await postAdjudicate({
        claim,
        actor_id: "web-demo-json",
        actor_role: "claims_processor",
        action: "adjudicate",
      });
      setStatus("", false);
      renderResult(data);
    } catch (e) {
      setStatus("", false);
      setError(e.message || String(e));
    }
  });

  document.getElementById("btn-load-sample").addEventListener("click", () => {
    document.getElementById("claim-json").value = SAMPLE_JSON;
  });

  document.getElementById("btn-copy").addEventListener("click", async () => {
    const t = document.getElementById("result-raw").textContent;
    try {
      await navigator.clipboard.writeText(t);
      setStatus("Copied raw JSON to clipboard.", true);
      setTimeout(() => setStatus("", false), 2000);
    } catch {
      setError("Clipboard not available");
    }
  });

  document.getElementById("btn-print").addEventListener("click", () => {
    window.print();
  });

  initTabs();
  initGuided();
  initSettings();
  document.getElementById("claim-json").value = SAMPLE_JSON;
})();
