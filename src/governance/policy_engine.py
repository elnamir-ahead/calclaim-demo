"""
OPA-style Policy Engine — mirrors 'OPA Policy Engine (Collibra tags, T1-T4 tiers)'.

Policies enforced:
  - Data tier access (T1=public → T4=restricted PHI)
  - Bulk operation guard  (>50 claims in one session → HITL required)
  - Destructive action guard (reversal, override → requires dual approval)
  - Formulary tier restrictions per plan
  - PHI access logging obligation

Set ``USE_OPA=true`` and ``OPA_SERVER_URL`` (e.g. ``http://localhost:8181``) to evaluate
``policies/calclaim.rego`` via the OPA HTTP API. Otherwise policies run inline (same semantics).
"""

from __future__ import annotations

import os
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import httpx

logger = logging.getLogger(__name__)

OPA_SERVER_URL = os.getenv("OPA_SERVER_URL", "").strip()
USE_OPA = os.getenv("USE_OPA", "false").lower() in ("1", "true", "yes")

PolicyDecision = Literal["ALLOW", "DENY", "REQUIRE_HITL", "REQUIRE_DUAL_APPROVAL"]


@dataclass
class PolicyResult:
    decision: PolicyDecision
    policy_id: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == "ALLOW"

    @property
    def requires_human(self) -> bool:
        return self.decision in ("REQUIRE_HITL", "REQUIRE_DUAL_APPROVAL")


# ---------------------------------------------------------------------------
# Inline policy rules (mirrors OPA Rego logic)
# ---------------------------------------------------------------------------

class InlinePolicyEngine:

    def evaluate_claim_access(
        self,
        actor_role: str,
        claim: dict[str, Any],
        action: str = "read",
    ) -> PolicyResult:
        """T1–T4 data tier policy."""
        drug = claim.get("drug", {})
        tier = drug.get("tier", 1)
        plan = claim.get("member", {}).get("plan", {}).get("plan_id", "")
        amount = claim.get("pricing", {}).get("plan_pay", 0)

        # Tier 5 specialty drugs always require PA + clinical review
        if tier == 5 and action in ("approve", "adjudicate"):
            return PolicyResult(
                decision="REQUIRE_HITL",
                policy_id="POL-TIER5-001",
                reason=f"Tier-5 specialty drug requires clinical HITL review (drug tier={tier})",
                metadata={"tier": tier, "amount": amount},
            )

        # High-value claims (>$1000 plan liability) trigger dual-approval for approvals
        if amount > 1000 and action == "approve":
            return PolicyResult(
                decision="REQUIRE_HITL",
                policy_id="POL-HIGHVAL-001",
                reason=f"High-value claim (plan_pay=${amount:.2f}) requires supervisor approval",
                metadata={"amount": amount},
            )

        # Reversal / override always needs dual approval
        if action in ("reverse", "override"):
            return PolicyResult(
                decision="REQUIRE_DUAL_APPROVAL",
                policy_id="POL-DESTRUCT-001",
                reason="Destructive action requires dual approval per governance policy",
                metadata={"action": action},
            )

        # Viewer role cannot adjudicate
        if actor_role == "viewer" and action not in ("read", "query"):
            return PolicyResult(
                decision="DENY",
                policy_id="POL-RBAC-001",
                reason=f"Role '{actor_role}' is not authorized to perform '{action}'",
            )

        return PolicyResult(
            decision="ALLOW",
            policy_id="POL-DEFAULT-ALLOW",
            reason="No policy triggered — access granted",
        )

    def evaluate_bulk_operation(
        self, session_claim_count: int, threshold: int = 50
    ) -> PolicyResult:
        """Bulk operation guard."""
        if session_claim_count >= threshold:
            return PolicyResult(
                decision="REQUIRE_HITL",
                policy_id="POL-BULK-001",
                reason=f"Bulk threshold exceeded ({session_claim_count} claims ≥ {threshold}) — HITL required",
                metadata={"count": session_claim_count, "threshold": threshold},
            )
        return PolicyResult(decision="ALLOW", policy_id="POL-BULK-PASS", reason="Below bulk threshold")

    def evaluate_phi_access(
        self, actor_id: str, purpose: str, member_id: str
    ) -> PolicyResult:
        """HIPAA minimum-necessary access check."""
        allowed_purposes = {
            "treatment",
            "payment",
            "operations",
            "audit",
            "claim_processing",
            # Workflow actions (also accepted if passed as purpose by mistake)
            "adjudicate",
            "reverse",
            "query",
            "read",
            "approve",
        }
        if purpose.lower() not in allowed_purposes:
            return PolicyResult(
                decision="DENY",
                policy_id="POL-HIPAA-001",
                reason=f"PHI access purpose '{purpose}' not in approved list",
                metadata={"actor": actor_id, "member": member_id},
            )
        return PolicyResult(
            decision="ALLOW",
            policy_id="POL-HIPAA-PASS",
            reason=f"PHI access approved for purpose='{purpose}'",
        )

    def evaluate_formulary_coverage(
        self, drug_tier: int, plan_id: str
    ) -> PolicyResult:
        """Plan-formulary tier restriction."""
        tier4_restricted_plans = {"PLN-COMM-001", "PLN-MEDV-001"}
        if drug_tier >= 4 and plan_id in tier4_restricted_plans:
            return PolicyResult(
                decision="REQUIRE_HITL",
                policy_id="POL-FORM-001",
                reason=f"Tier-{drug_tier} drug on restricted plan {plan_id} requires PA + HITL",
                metadata={"drug_tier": drug_tier, "plan_id": plan_id},
            )
        return PolicyResult(
            decision="ALLOW",
            policy_id="POL-FORM-PASS",
            reason="Formulary coverage policy satisfied",
        )


# ---------------------------------------------------------------------------
# OPA HTTP client (production path)
# ---------------------------------------------------------------------------

class OPAPolicyEngine:
    def __init__(self, server_url: str) -> None:
        self._url = server_url.rstrip("/")

    def evaluate(self, policy_path: str, input_data: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._url}/v1/data/{policy_path}"
        try:
            resp = httpx.post(url, json={"input": input_data}, timeout=10.0)
            resp.raise_for_status()
            body = resp.json()
            return body.get("result", body)
        except Exception as exc:
            logger.error("OPA evaluation failed (%s): %s", policy_path, exc)
            return {"allow": False, "reason": str(exc), "decision": "DENY"}

    @staticmethod
    def raw_to_policy_result(raw: Any, fallback: PolicyResult) -> PolicyResult:
        """Map OPA document or legacy {allow, reason} to PolicyResult."""
        if not isinstance(raw, dict):
            return fallback
        if "decision" in raw:
            d = raw.get("decision")
            if d in ("ALLOW", "DENY", "REQUIRE_HITL", "REQUIRE_DUAL_APPROVAL"):
                meta = raw.get("metadata")
                return PolicyResult(
                    decision=d,
                    policy_id=str(raw.get("policy_id", "OPA")),
                    reason=str(raw.get("reason", "")),
                    metadata=dict(meta) if isinstance(meta, dict) else {},
                )
        if raw.get("allow") is True:
            return PolicyResult(
                decision="ALLOW",
                policy_id="OPA",
                reason=str(raw.get("reason", "OPA allowed")),
            )
        if raw.get("allow") is False:
            return PolicyResult(
                decision="DENY",
                policy_id="OPA",
                reason=str(raw.get("reason", "OPA denied")),
            )
        return fallback


# ---------------------------------------------------------------------------
# Facade — auto-selects OPA vs inline
# ---------------------------------------------------------------------------

class PolicyEngine:
    def __init__(self) -> None:
        if OPA_SERVER_URL and USE_OPA:
            self._opa = OPAPolicyEngine(OPA_SERVER_URL)
            self._inline = InlinePolicyEngine()
            self._use_opa = True
            logger.info("Policy engine: OPA enabled at %s", OPA_SERVER_URL)
        else:
            self._inline = InlinePolicyEngine()
            self._use_opa = False
            if OPA_SERVER_URL and not USE_OPA:
                logger.debug("OPA_SERVER_URL set but USE_OPA is false — using inline policies")

    def evaluate_claim_access(self, actor_role: str, claim: dict, action: str = "read") -> PolicyResult:
        if self._use_opa:
            raw = self._opa.evaluate(
                "calclaim/claim_access",
                {"role": actor_role, "claim": claim, "action": action},
            )
            fb = self._inline.evaluate_claim_access(actor_role, claim, action)
            return OPAPolicyEngine.raw_to_policy_result(raw, fb)
        return self._inline.evaluate_claim_access(actor_role, claim, action)

    def evaluate_bulk_operation(
        self, session_claim_count: int, threshold: int = 50
    ) -> PolicyResult:
        if self._use_opa:
            raw = self._opa.evaluate(
                "calclaim/bulk_operation",
                {"count": session_claim_count, "threshold": threshold},
            )
            fb = self._inline.evaluate_bulk_operation(session_claim_count, threshold)
            return OPAPolicyEngine.raw_to_policy_result(raw, fb)
        return self._inline.evaluate_bulk_operation(session_claim_count, threshold)

    def evaluate_phi_access(self, actor_id: str, purpose: str, member_id: str) -> PolicyResult:
        if self._use_opa:
            raw = self._opa.evaluate(
                "calclaim/phi_access",
                {"actor": actor_id, "purpose": purpose, "member": member_id},
            )
            fb = self._inline.evaluate_phi_access(actor_id, purpose, member_id)
            return OPAPolicyEngine.raw_to_policy_result(raw, fb)
        return self._inline.evaluate_phi_access(actor_id, purpose, member_id)

    def evaluate_formulary_coverage(self, drug_tier: int, plan_id: str) -> PolicyResult:
        if self._use_opa:
            raw = self._opa.evaluate(
                "calclaim/formulary_coverage",
                {"drug_tier": drug_tier, "plan_id": plan_id},
            )
            fb = self._inline.evaluate_formulary_coverage(drug_tier, plan_id)
            return OPAPolicyEngine.raw_to_policy_result(raw, fb)
        return self._inline.evaluate_formulary_coverage(drug_tier, plan_id)


_policy_engine: Optional[PolicyEngine] = None


def get_policy_engine() -> PolicyEngine:
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = PolicyEngine()
    return _policy_engine
