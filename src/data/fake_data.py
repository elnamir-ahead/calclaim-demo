"""
Fake data generator for CalcClaim demo.
Generates realistic pharmacy benefit management (PBM) claims data.
"""

import json
import random
import uuid
from datetime import datetime, timedelta
from typing import Any

from faker import Faker

fake = Faker()
random.seed(42)


# ---------------------------------------------------------------------------
# Reference tables
# ---------------------------------------------------------------------------

DRUG_CATALOG = [
    {"ndc": "00093-0058-01", "name": "Lisinopril 10mg", "gpi": "36200010100310", "tier": 1, "class": "ACE Inhibitor", "brand": False, "cost": 12.50},
    {"ndc": "00071-0155-23", "name": "Lipitor 20mg", "gpi": "39400010201010", "tier": 3, "class": "Statin", "brand": True, "cost": 145.00},
    {"ndc": "68180-0513-01", "name": "Atorvastatin 20mg", "gpi": "39400010201010", "tier": 2, "class": "Statin", "brand": False, "cost": 18.75},
    {"ndc": "00085-0221-01", "name": "Metformin 500mg", "gpi": "27600030100310", "tier": 1, "class": "Biguanide", "brand": False, "cost": 8.00},
    {"ndc": "00088-2218-47", "name": "Lantus SoloStar 100u/mL", "gpi": "27250020100350", "tier": 3, "class": "Insulin", "brand": True, "cost": 312.00},
    {"ndc": "00002-1433-80", "name": "Trulicity 1.5mg/0.5mL", "gpi": "27250035500310", "tier": 3, "class": "GLP-1 Agonist", "brand": True, "cost": 890.00},
    {"ndc": "59762-3220-01", "name": "Amlodipine 5mg", "gpi": "40200010100310", "tier": 1, "class": "CCB", "brand": False, "cost": 9.25},
    {"ndc": "00228-2880-50", "name": "Omeprazole 20mg", "gpi": "49270010100310", "tier": 1, "class": "PPI", "brand": False, "cost": 11.00},
    {"ndc": "00003-0894-20", "name": "Eliquis 5mg", "gpi": "83600040104010", "tier": 3, "class": "Anticoagulant", "brand": True, "cost": 520.00},
    {"ndc": "00078-0359-20", "name": "Entresto 49-51mg", "gpi": "36200080100110", "tier": 3, "class": "ARNI", "brand": True, "cost": 650.00},
    {"ndc": "00006-0021-31", "name": "Keytruda 100mg/4mL", "gpi": "91250030100310", "tier": 5, "class": "Immunotherapy", "brand": True, "cost": 12500.00, "requires_pa": True},
    {"ndc": "50436-0001-01", "name": "Ozempic 1mg/dose", "gpi": "27250035500320", "tier": 4, "class": "GLP-1 Agonist", "brand": True, "cost": 935.00, "requires_pa": True},
]

DIAGNOSIS_CODES = [
    {"code": "E11.9", "description": "Type 2 diabetes mellitus without complications"},
    {"code": "I10", "description": "Essential hypertension"},
    {"code": "E78.5", "description": "Hyperlipidemia, unspecified"},
    {"code": "I50.9", "description": "Heart failure, unspecified"},
    {"code": "N18.3", "description": "Chronic kidney disease, stage 3"},
    {"code": "Z79.4", "description": "Long-term (current) use of insulin"},
    {"code": "E11.65", "description": "Type 2 diabetes mellitus with hyperglycemia"},
    {"code": "I48.91", "description": "Unspecified atrial fibrillation"},
]

PHARMACIES = [
    {"npi": "1245319599", "name": "CVS Pharmacy #7842", "ncpdp": "5781234", "type": "retail", "state": "TX"},
    {"npi": "1851381859", "name": "Walgreens #03921", "ncpdp": "5782456", "type": "retail", "state": "IL"},
    {"npi": "1366429596", "name": "Navitus Specialty Pharmacy", "ncpdp": "5783678", "type": "specialty", "state": "WI"},
    {"npi": "1467472362", "name": "Rite Aid #4512", "ncpdp": "5784890", "type": "retail", "state": "CA"},
    {"npi": "1023189010", "name": "Express Scripts Mail Service", "ncpdp": "5785012", "type": "mail", "state": "MO"},
]

PLANS = [
    {"plan_id": "PLN-COMM-001", "name": "Commercial PPO Gold", "bin": "610591", "pcn": "NAVITUS", "group": "GRP001"},
    {"plan_id": "PLN-COMM-002", "name": "Commercial HMO Silver", "bin": "610591", "pcn": "NAVITUS", "group": "GRP002"},
    {"plan_id": "PLN-MEDV-001", "name": "Medicare Part D PDP", "bin": "004336", "pcn": "MEDPBM", "group": "GRPMED"},
    {"plan_id": "PLN-SELF-001", "name": "Self-Insured Employer Plan", "bin": "610591", "pcn": "NAVITUS", "group": "GRP003"},
]

REJECT_CODES = {
    "75": "Prior authorization required",
    "76": "Plan limitations exceeded",
    "79": "Refill too soon",
    "88": "DUR reject — drug interaction",
    "70": "Product/service not covered",
    "41": "Submit bill to other processor first",
}

REVERSAL_REASONS = [
    "Member request",
    "Pharmacy billing error",
    "Duplicate claim",
    "Prior auth retroactively approved",
]


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def generate_member() -> dict[str, Any]:
    dob = fake.date_of_birth(minimum_age=18, maximum_age=85)
    return {
        "member_id": f"MBR-{fake.numerify('########')}",
        "first_name": fake.first_name(),
        "last_name": fake.last_name(),
        "dob": dob.isoformat(),
        "gender": random.choice(["M", "F"]),
        "ssn_last4": fake.numerify("####"),   # partial — PII demo
        "address": {
            "street": fake.street_address(),
            "city": fake.city(),
            "state": fake.state_abbr(),
            "zip": fake.zipcode(),
        },
        "email": fake.email(),
        "phone": fake.phone_number(),
        "plan": random.choice(PLANS),
        "relationship_code": random.choice(["01", "02", "03"]),
        "effective_date": (datetime.now() - timedelta(days=random.randint(30, 730))).date().isoformat(),
        "termination_date": None,
    }


def generate_prescriber() -> dict[str, Any]:
    return {
        "npi": fake.numerify("##########"),
        "first_name": fake.first_name(),
        "last_name": f"Dr. {fake.last_name()}",
        "dea": f"B{fake.lexify('?').upper()}{fake.numerify('#######')}",
        "specialty": random.choice([
            "Internal Medicine", "Cardiology", "Endocrinology",
            "Oncology", "Primary Care", "Neurology"
        ]),
        "state_license": fake.lexify("??") + fake.numerify("######"),
    }


def generate_claim(member: dict, scenario: str = "auto") -> dict[str, Any]:
    drug = random.choice(DRUG_CATALOG)
    pharmacy = random.choice(PHARMACIES)
    prescriber = generate_prescriber()
    diagnosis = random.choice(DIAGNOSIS_CODES)
    days_supply = random.choice([30, 60, 90])
    quantity = random.choice([30, 60, 90, 180])
    submitted_date = datetime.now() - timedelta(days=random.randint(0, 90))

    # Calculate copay and plan pay based on tier
    tier_copays = {1: 10.00, 2: 35.00, 3: 70.00, 4: 100.00, 5: 200.00}
    tier = drug.get("tier", 2)
    copay = tier_copays.get(tier, 35.00)
    plan_pay = max(0, drug["cost"] - copay)

    # Force certain scenarios for demo diversity (explicit scenario wins over random drug flags)
    if scenario == "approved":
        status = "approved"
    elif scenario == "prior_auth_required" or drug.get("requires_pa"):
        status = random.choice(["rejected_pending_pa", "approved_with_pa"])
    elif scenario == "refill_too_soon":
        status = "rejected_refill_too_soon"
    elif scenario == "drug_interaction":
        status = "rejected_dur"
    else:
        status = random.choices(
            ["approved", "approved", "approved", "rejected_pending_pa",
             "rejected_refill_too_soon", "reversed"],
            weights=[60, 0, 0, 15, 10, 15],
            k=1
        )[0]

    reject_code = None
    reject_message = None
    if "rejected" in status:
        if "pa" in status:
            reject_code = "75"
        elif "refill" in status:
            reject_code = "79"
        elif "dur" in status:
            reject_code = "88"
        reject_message = REJECT_CODES.get(reject_code, "Unknown rejection")

    return {
        "claim_id": f"CLM-{uuid.uuid4().hex[:12].upper()}",
        "transaction_id": f"TXN-{uuid.uuid4().hex[:16].upper()}",
        "submitted_at": submitted_date.isoformat(),
        "processed_at": (submitted_date + timedelta(seconds=random.randint(200, 400))).isoformat(),
        "status": status,
        "member": member,
        "drug": drug,
        "pharmacy": pharmacy,
        "prescriber": prescriber,
        "diagnosis": diagnosis,
        "days_supply": days_supply,
        "quantity_dispensed": quantity,
        "fill_number": random.randint(1, 6),
        "pricing": {
            "ingredient_cost": round(drug["cost"], 2),
            "dispensing_fee": 2.50,
            "copay": copay if "approved" in status else 0.0,
            "plan_pay": round(plan_pay, 2) if "approved" in status else 0.0,
            "member_pay": copay if "approved" in status else 0.0,
            "total_submitted": round(drug["cost"] + 2.50, 2),
            "awp": round(drug["cost"] * 1.2, 2),
        },
        "reject_code": reject_code,
        "reject_message": reject_message,
        "prior_auth": {
            "required": drug.get("requires_pa", False),
            "number": f"PA-{fake.numerify('########')}" if "approved_with_pa" in status else None,
            "approved_date": submitted_date.date().isoformat() if "approved_with_pa" in status else None,
        },
        "dur_alerts": _generate_dur_alerts() if status == "rejected_dur" else [],
        "audit_trail": [],
    }


def _generate_dur_alerts() -> list[dict]:
    return [{
        "alert_type": random.choice(["DD", "DC", "TD", "PA"]),
        "description": random.choice([
            "Duplicate drug therapy detected",
            "Potential drug-drug interaction: Warfarin + Aspirin",
            "Therapeutic duplication: two ACE inhibitors on profile",
            "Drug allergy on file: Sulfa",
        ]),
        "severity": random.choice(["high", "moderate", "low"]),
        "professional_service_code": random.choice(["M0", "P0", "R0"]),
    }]


# ---------------------------------------------------------------------------
# Dataset builder
# ---------------------------------------------------------------------------

def generate_demo_dataset(
    n_members: int = 20,
    claims_per_member: int = 3,
) -> dict[str, Any]:
    members = [generate_member() for _ in range(n_members)]
    all_claims = []

    scenarios = [
        "approved", "approved", "approved",
        "prior_auth_required", "refill_too_soon", "drug_interaction",
    ]

    for member in members:
        for i in range(claims_per_member):
            scenario = scenarios[i % len(scenarios)]
            claim = generate_claim(member, scenario=scenario)
            all_claims.append(claim)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "demo_version": "v2",
        "members": members,
        "claims": all_claims,
        "summary": {
            "total_claims": len(all_claims),
            "approved": sum(1 for c in all_claims if "approved" in c["status"]),
            "rejected": sum(1 for c in all_claims if "rejected" in c["status"]),
            "reversed": sum(1 for c in all_claims if c["status"] == "reversed"),
            "total_plan_liability": round(
                sum(c["pricing"]["plan_pay"] for c in all_claims), 2
            ),
        },
    }


if __name__ == "__main__":
    import sys
    dataset = generate_demo_dataset()
    print(json.dumps(dataset["summary"], indent=2))
    output_path = "src/data/sample_claims.json"
    with open(output_path, "w") as f:
        json.dump(dataset, f, indent=2)
    print(f"Written {len(dataset['claims'])} claims to {output_path}", file=sys.stderr)
