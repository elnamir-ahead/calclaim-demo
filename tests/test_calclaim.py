"""
CalcClaim unit + integration tests.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from src.data.fake_data import generate_demo_dataset, generate_member, generate_claim
from src.governance.pii_scrubber import PHIScrubber
from src.governance.audit_logger import AuditLogger, AuditEvent
from src.governance.policy_engine import InlinePolicyEngine, PolicyResult
from src.governance.hitl_gate import HITLGate


# ---------------------------------------------------------------------------
# Data generation tests
# ---------------------------------------------------------------------------

class TestFakeData:
    def test_generates_members(self):
        member = generate_member()
        assert member["member_id"].startswith("MBR-")
        assert member["plan"]["plan_id"].startswith("PLN-")
        assert member["dob"] is not None

    def test_generates_claims(self):
        member = generate_member()
        claim = generate_claim(member, scenario="approved")
        assert claim["claim_id"].startswith("CLM-")
        assert claim["status"] == "approved"
        assert claim["pricing"]["plan_pay"] > 0

    def test_generates_rejected_pa_claim(self):
        member = generate_member()
        claim = generate_claim(member, scenario="prior_auth_required")
        assert "pa" in claim["status"] or "rejected" in claim["status"]

    def test_dataset_summary(self):
        dataset = generate_demo_dataset(n_members=5, claims_per_member=2)
        assert dataset["summary"]["total_claims"] == 10
        assert dataset["summary"]["total_plan_liability"] >= 0


# ---------------------------------------------------------------------------
# PII scrubber tests
# ---------------------------------------------------------------------------

class TestPHIScrubber:
    def setup_method(self):
        self.scrubber = PHIScrubber()

    def test_scrubs_ssn(self):
        text = "Patient SSN: 123-45-6789"
        scrubbed, entities = self.scrubber.scrub_text(text)
        assert "123-45-6789" not in scrubbed
        assert "SSN" in entities

    def test_scrubs_email(self):
        text = "Contact: john.doe@example.com for updates"
        scrubbed, entities = self.scrubber.scrub_text(text)
        assert "john.doe@example.com" not in scrubbed
        assert "EMAIL" in entities

    def test_scrubs_dob(self):
        text = "DOB: 1985-03-15"
        scrubbed, entities = self.scrubber.scrub_text(text)
        assert "1985-03-15" not in scrubbed

    def test_masks_member_pii(self):
        member = {
            "member_id": "MBR-12345678",
            "first_name": "John",
            "last_name": "Doe",
            "dob": "1980-01-15",
            "ssn_last4": "6789",
            "plan": {"plan_id": "PLN-COMM-001"},
            "address": {"state": "TX", "zip": "78701"},
        }
        masked = self.scrubber.mask_member_pii(member)
        assert masked["member_id"] == "MBR-12345678"
        assert masked["dob"] == "[DOB-REDACTED]"
        assert masked["ssn_last4"] == "[SSN4-REDACTED]"
        assert masked["first_name"] == "J***"

    def test_clean_text_unchanged(self):
        text = "Atorvastatin 20mg, Tier 2, covered under commercial plan"
        scrubbed, entities = self.scrubber.scrub_text(text)
        assert scrubbed == text
        assert not entities


# ---------------------------------------------------------------------------
# Audit logger tests
# ---------------------------------------------------------------------------

class TestAuditLogger:
    def setup_method(self):
        self.audit = AuditLogger()  # DEMO_MODE=True by default in test env

    def test_log_creates_event(self):
        event_id = self.audit.log(
            "CLAIM_RECEIVED",
            claim_id="CLM-TEST001",
            actor="test",
            details={"test": True},
        )
        assert event_id is not None
        assert len(event_id) == 36  # UUID format

    def test_audit_trail_retrieval(self):
        self.audit.log("CLAIM_RECEIVED", "CLM-TRAIL001", "test", {})
        self.audit.log("ADJUDICATION_STARTED", "CLM-TRAIL001", "agent", {})
        self.audit.log("ADJUDICATION_COMPLETE", "CLM-TRAIL001", "agent", {})

        trail = self.audit.get_claim_trail("CLM-TRAIL001")
        assert len(trail) >= 3
        assert all(e["claim_id"] == "CLM-TRAIL001" for e in trail)

    def test_integrity_hash(self):
        event = AuditEvent("CLAIM_RECEIVED", "CLM-HASH001", "test", {})
        record = event.to_dict()
        assert len(record["integrity_hash"]) == 64  # SHA-256 hex

    def test_immutability_via_condition(self):
        # Different events same claim should all be stored
        for i in range(3):
            self.audit.log("GOVERNANCE_CHECK", f"CLM-IMM001", "system",
                           {"step": i}, outcome="ALLOW")
        trail = self.audit.get_claim_trail("CLM-IMM001")
        assert len(trail) >= 3


# ---------------------------------------------------------------------------
# Policy engine tests
# ---------------------------------------------------------------------------

class TestPolicyEngine:
    def setup_method(self):
        self.engine = InlinePolicyEngine()

    def _make_claim(self, tier=2, plan_id="PLN-COMM-001", plan_pay=100.0):
        return {
            "drug": {"tier": tier},
            "member": {"plan": {"plan_id": plan_id}},
            "pricing": {"plan_pay": plan_pay},
        }

    def test_allows_normal_claim(self):
        result = self.engine.evaluate_claim_access(
            "claims_processor", self._make_claim(tier=2, plan_pay=50.0)
        )
        assert result.allowed

    def test_hitl_for_tier5_drug(self):
        result = self.engine.evaluate_claim_access(
            "claims_processor",
            self._make_claim(tier=5, plan_pay=12500.0),
            action="adjudicate",
        )
        assert result.requires_human
        assert "5" in result.reason

    def test_hitl_for_high_value(self):
        result = self.engine.evaluate_claim_access(
            "claims_processor",
            self._make_claim(tier=3, plan_pay=1500.0),
            action="approve",
        )
        assert result.requires_human

    def test_dual_approval_for_reversal(self):
        result = self.engine.evaluate_claim_access(
            "supervisor", self._make_claim(), action="reverse"
        )
        assert result.decision == "REQUIRE_DUAL_APPROVAL"

    def test_viewer_denied_adjudication(self):
        result = self.engine.evaluate_claim_access(
            "viewer", self._make_claim(), action="adjudicate"
        )
        assert result.decision == "DENY"

    def test_bulk_threshold(self):
        result = self.engine.evaluate_bulk_operation(session_claim_count=51)
        assert result.requires_human

    def test_phi_access_valid_purpose(self):
        result = self.engine.evaluate_phi_access("user1", "payment", "MBR-001")
        assert result.allowed

    def test_phi_access_invalid_purpose(self):
        result = self.engine.evaluate_phi_access("user1", "marketing", "MBR-001")
        assert result.decision == "DENY"


# ---------------------------------------------------------------------------
# HITL gate tests
# ---------------------------------------------------------------------------

class TestHITLGate:
    def setup_method(self):
        self.hitl = HITLGate()

    def test_trigger_creates_request(self):
        req = self.hitl.trigger(
            "HIGH_VALUE_CLAIM",
            claim_id="CLM-HITL001",
            reason="Plan pay $1500 exceeds threshold",
        )
        assert req.request_id is not None
        assert req.claim_id == "CLM-HITL001"

    def test_demo_auto_resolves(self):
        req = self.hitl.trigger("HIGH_VALUE_CLAIM", "CLM-HITL002", "test")
        assert req.resolution in ("APPROVED", "DENIED")

    def test_phi_detected_auto_denied(self):
        req = self.hitl.trigger("PHI_DETECTED", "CLM-HITL003", "PHI in output")
        assert req.resolution == "DENIED"

    def test_manual_resolve(self):
        req = self.hitl.trigger.__wrapped__ if hasattr(self.hitl.trigger, "__wrapped__") else None
        # Direct test
        gate = HITLGate()
        r = gate.trigger("POLICY_DENY", "CLM-HITL004", "test")
        resolved = gate.resolve(r.request_id, "APPROVED", "test-reviewer", "Looks fine")
        assert resolved is not None
        assert resolved.resolution == "APPROVED"
        assert resolved.resolved_by == "test-reviewer"


# ---------------------------------------------------------------------------
# Five pillars — demo API helpers
# ---------------------------------------------------------------------------


class TestPillarStatus:
    def test_build_pillar_report_has_five_pillars(self):
        from src.utils.pillar_status import build_pillar_demo_report

        r = build_pillar_demo_report()
        assert r["schema_version"] == "1.0"
        assert set(r["pillars"].keys()) == {
            "llm_gateway",
            "evaluation",
            "governance",
            "mcp",
            "observability",
        }

    def test_policy_viewer_adjudicate_denied(self):
        eng = InlinePolicyEngine()
        claim = {
            "drug": {"tier": 1},
            "member": {"plan": {"plan_id": "PLN-DEMO"}},
            "pricing": {"plan_pay": 50.0},
        }
        res = eng.evaluate_claim_access("viewer", claim, "adjudicate")
        assert res.decision == "DENY"
        assert "POL-RBAC" in res.policy_id


# calcClaim2 PDF-aligned component tests: see tests/test_calc_claim2_components.py
