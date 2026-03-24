# CalcClaim OPA — mutually exclusive claim_access rules (one matches).
# Run: cd policies && opa run --server .
# Bundle: opa build -b . -o bundle.tar.gz

package calclaim

viewer_deny {
	input.role == "viewer"
	not input.action == "read"
	not input.action == "query"
}

tier5_adjudicate {
	not viewer_deny
	input.claim.drug.tier == 5
	input.action == "adjudicate"
}

tier5_approve {
	not viewer_deny
	input.claim.drug.tier == 5
	input.action == "approve"
}

high_value_approve {
	not viewer_deny
	not tier5_adjudicate
	not tier5_approve
	input.action == "approve"
	input.claim.pricing.plan_pay > 1000
}

destructive {
	not viewer_deny
	not tier5_adjudicate
	not tier5_approve
	not high_value_approve
	input.action == "reverse"
}

destructive_override {
	not viewer_deny
	not tier5_adjudicate
	not tier5_approve
	not high_value_approve
	not destructive
	input.action == "override"
}

claim_access := {"decision": "DENY", "policy_id": "OPA-RBAC-001", "reason": sprintf("Role '%s' cannot perform '%s'", [input.role, input.action])} {
	viewer_deny
}

claim_access := {"decision": "REQUIRE_HITL", "policy_id": "OPA-TIER5-001", "reason": "Tier-5 specialty requires clinical HITL"} {
	tier5_adjudicate
}

claim_access := {"decision": "REQUIRE_HITL", "policy_id": "OPA-TIER5-001", "reason": "Tier-5 specialty requires clinical HITL"} {
	tier5_approve
}

claim_access := {"decision": "REQUIRE_HITL", "policy_id": "OPA-HIGHVAL-001", "reason": sprintf("High plan liability (plan_pay=%v) requires supervisor", [input.claim.pricing.plan_pay])} {
	high_value_approve
}

claim_access := {"decision": "REQUIRE_DUAL_APPROVAL", "policy_id": "OPA-DESTRUCT-001", "reason": "Destructive action requires dual approval"} {
	destructive
}

claim_access := {"decision": "REQUIRE_DUAL_APPROVAL", "policy_id": "OPA-DESTRUCT-001", "reason": "Destructive action requires dual approval"} {
	destructive_override
}

claim_access := {"decision": "ALLOW", "policy_id": "OPA-DEFAULT-ALLOW", "reason": "No claim_access rule triggered"} {
	not viewer_deny
	not tier5_adjudicate
	not tier5_approve
	not high_value_approve
	not destructive
	not destructive_override
}

# --- bulk_operation ---
bulk_operation := {"decision": "REQUIRE_HITL", "policy_id": "OPA-BULK-001", "reason": sprintf("Bulk threshold exceeded (%d >= %d)", [input.count, input.threshold])} {
	input.count >= input.threshold
}

bulk_operation := {"decision": "ALLOW", "policy_id": "OPA-BULK-PASS", "reason": "Below bulk threshold"} {
	input.count < input.threshold
}

# --- phi_access ---
approved_purpose {
	p := lower(input.purpose)
	p == "treatment"
}

approved_purpose {
	p := lower(input.purpose)
	p == "payment"
}

approved_purpose {
	p := lower(input.purpose)
	p == "operations"
}

approved_purpose {
	p := lower(input.purpose)
	p == "audit"
}

approved_purpose {
	p := lower(input.purpose)
	p == "claim_processing"
}

approved_purpose {
	p := lower(input.purpose)
	p == "adjudicate"
}

approved_purpose {
	p := lower(input.purpose)
	p == "reverse"
}

approved_purpose {
	p := lower(input.purpose)
	p == "query"
}

approved_purpose {
	p := lower(input.purpose)
	p == "read"
}

approved_purpose {
	p := lower(input.purpose)
	p == "approve"
}

phi_access := {"decision": "DENY", "policy_id": "OPA-HIPAA-001", "reason": sprintf("PHI purpose '%s' not approved", [input.purpose])} {
	not approved_purpose
}

phi_access := {"decision": "ALLOW", "policy_id": "OPA-HIPAA-PASS", "reason": sprintf("PHI access for purpose '%s'", [input.purpose])} {
	approved_purpose
}

# --- formulary_coverage ---
formulary_hitl {
	input.drug_tier >= 4
	input.plan_id == "PLN-COMM-001"
}

formulary_hitl {
	input.drug_tier >= 4
	input.plan_id == "PLN-MEDV-001"
}

formulary_coverage := {"decision": "REQUIRE_HITL", "policy_id": "OPA-FORM-001", "reason": sprintf("Tier %d on restricted plan requires PA + HITL", [input.drug_tier])} {
	formulary_hitl
}

formulary_coverage := {"decision": "ALLOW", "policy_id": "OPA-FORM-PASS", "reason": "Formulary policy satisfied"} {
	not formulary_hitl
}
