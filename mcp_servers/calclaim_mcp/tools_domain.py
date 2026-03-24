"""
Demo domain logic for MCP tools (no PHI; synthetic formulary hints).

Production: replace with calls to formulary service, OPA, etc.
"""

from __future__ import annotations

import re
from typing import Any

# Synthetic NDC → tier / PA hints (demo only). Keys = digits only (hyphens stripped).
# Aligned with ``src/data/fake_data.DRUG_CATALOG`` NDCs where possible.
_NDC_HINTS: dict[str, dict[str, Any]] = {
    "00093005801": {"drug_name": "Lisinopril 10mg", "tier": 1, "brand": False, "pa_typically_required": False},
    "00071015523": {"drug_name": "Lipitor 20mg", "tier": 3, "brand": True, "pa_typically_required": False},
    "68180051301": {"drug_name": "Atorvastatin 20mg", "tier": 2, "brand": False, "pa_typically_required": False},
    "00085022101": {"drug_name": "Metformin 500mg", "tier": 1, "brand": False, "pa_typically_required": False},
    "00088221847": {"drug_name": "Lantus SoloStar 100u/mL", "tier": 3, "brand": True, "pa_typically_required": False},
    "00002143380": {"drug_name": "Trulicity 1.5mg/0.5mL", "tier": 4, "brand": True, "pa_typically_required": True},
    "59762322001": {"drug_name": "Amlodipine 5mg", "tier": 1, "brand": False, "pa_typically_required": False},
    "00228288050": {"drug_name": "Omeprazole 20mg", "tier": 1, "brand": False, "pa_typically_required": False},
    "00003089421": {"drug_name": "Eliquis 5mg", "tier": 3, "brand": True, "pa_typically_required": False},
    "00078035920": {"drug_name": "Entresto 49-51mg", "tier": 3, "brand": True, "pa_typically_required": False},
    "00006002131": {"drug_name": "Keytruda 100mg/4mL", "tier": 5, "brand": True, "pa_typically_required": True},
    "50436000101": {"drug_name": "Ozempic 1mg/dose", "tier": 4, "brand": True, "pa_typically_required": True},
}


def formulary_lookup(ndc: str, plan_code: str = "commercial_ppo") -> dict[str, Any]:
    """Normalize NDC and return demo tier/coverage hints."""
    raw = (ndc or "").strip().replace("-", "")
    if not raw.isdigit() or len(raw) not in (11, 8):
        return {
            "ok": False,
            "error": "ndc must be 8 or 11 digits (hyphens optional)",
            "ndc_normalized": raw,
        }
    info = _NDC_HINTS.get(raw)
    if not info:
        return {
            "ok": True,
            "ndc_normalized": raw,
            "plan_code": plan_code,
            "note": "No demo row — in production resolve against live formulary",
            "tier": None,
            "pa_typically_required": None,
        }
    return {
        "ok": True,
        "ndc_normalized": raw,
        "plan_code": plan_code,
        **info,
    }


def demo_ndc_list() -> list[dict[str, Any]]:
    return [{"ndc": k, **v} for k, v in _NDC_HINTS.items()]


def validate_claim_id_format(claim_id: str) -> dict[str, Any]:
    """Opaque ID format check only (no database)."""
    cid = (claim_id or "").strip()
    if re.fullmatch(r"CLM-[0-9A-F]{12}", cid, re.I):
        return {"ok": True, "claim_id": cid.upper()}
    return {
        "ok": False,
        "error": "expected pattern CLM- + 12 hex chars",
        "claim_id": cid,
    }
