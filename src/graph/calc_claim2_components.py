"""
calcClaim2-style modular claim calculation (demo implementation).

Mirrors the refactored C++ component names and responsibilities from the
calcClaim vs calcClaim2 comparison document. This is deterministic demo logic
aligned with ``safe_claim`` / fake_data shapes — not the production NCRX engine.

**Tests:** PDF-aligned categories live in ``tests/test_calc_claim2_components.py``;
see README § "Testing strategy (calcClaim vs calcClaim2 PDF)" for the mapping table.

Return codes (compatible with legacy calcClaim conventions used in the doc):
  0 = success
  3 = MBA recursion / margin limit (demo)
  6 = calculation / validation error
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# CostCalculationCore — ingredient, dispensing, MAC, AWP, lesser-of
# ---------------------------------------------------------------------------


@dataclass
class CostCalculationCore:
    """calcClaim2 CostCalculationCore (~520 lines in C++)."""

    def calculate_mac_pricing(self, m_tx: dict[str, Any]) -> dict[str, Any]:
        """C++: ``calculateMACPricing()`` — derive MAC relative to AWP when MAC absent."""
        pricing = m_tx.get("pricing") or {}
        ing = float(pricing.get("ingredient_cost") or 0)
        awp = float(pricing.get("awp") or (ing * 1.2 if ing else 0))
        mac = pricing.get("mac")
        if mac is None:
            mac = round(awp * 0.88, 2)
        else:
            mac = float(mac)
        wac = pricing.get("wac")
        return {
            "mac": mac,
            "awp": round(awp, 2),
            "wac": float(wac) if wac is not None else None,
        }

    def apply_lesser_of_logic(self, mac_info: dict[str, Any]) -> dict[str, Any]:
        """C++: ``applyLesserOfLogic()`` — select payable basis across price sources."""
        candidates: list[tuple[str, float]] = []
        for key, label in (("awp", "AWP"), ("mac", "MAC"), ("wac", "WAC")):
            v = mac_info.get(key)
            if v is not None:
                candidates.append((label, float(v)))
        if not candidates:
            return {"selected_price": 0.0, "price_source": "NONE"}
        label, selected = min(candidates, key=lambda x: x[1])
        return {"selected_price": round(selected, 2), "price_source": label}

    def calculate_basic_costs(self, m_tx: dict[str, Any], loop_type: int = 0) -> dict[str, Any]:
        """C++: ``calculateBasicCosts()`` — payable ingredient + dispensing fee."""
        pricing = m_tx.get("pricing") or {}
        ing_submitted = float(pricing.get("ingredient_cost") or 0)
        mac_info = self.calculate_mac_pricing(m_tx)
        lesser = self.apply_lesser_of_logic(mac_info)
        # Payable ingredient: lesser of submitted ingredient vs selected pricing benchmark (demo).
        ingredient_payable = min(ing_submitted, lesser["selected_price"]) if ing_submitted else lesser["selected_price"]
        disp = float(pricing.get("dispensing_fee") or 2.5)
        total = round(ingredient_payable + disp, 2)
        return {
            "ingredient_cost_payable": round(ingredient_payable, 2),
            "dispensing_fee": disp,
            "total_cost_basis": total,
            "lesser_of": lesser,
            "mac_pricing": mac_info,
            "loop_type": loop_type,
        }


# ---------------------------------------------------------------------------
# CopayCalculator
# ---------------------------------------------------------------------------


@dataclass
class CopayCalculator:
    """calcClaim2 CopayCalculator (~445 lines in C++)."""

    _tier_copays: dict[int, float] = field(
        default_factory=lambda: {1: 10.0, 2: 35.0, 3: 70.0, 4: 100.0, 5: 200.0}
    )

    def handle_compound_drugs(self, m_tx: dict[str, Any], base_copay: float) -> dict[str, Any]:
        """C++: ``handleCompoundDrugs()`` — adjust copay for compound constructs (demo)."""
        drug = m_tx.get("drug") or {}
        ingredients = drug.get("compound_ingredients") or []
        is_compound = bool(drug.get("is_compound") or drug.get("compound_count") or ingredients)
        if not is_compound:
            return {"copay_amount": base_copay, "compound": False}
        # Demo: slight uplift when multiple ingredients listed.
        n = max(len(ingredients), int(drug.get("compound_count") or 1))
        adjusted = round(base_copay * (1.0 + 0.05 * min(n, 4)), 2)
        return {"copay_amount": adjusted, "compound": True, "ingredient_count": n}

    def inflate_to_copay_or_uc(self, m_tx: dict[str, Any], copay_amount: float) -> float:
        """C++: ``inflateToCopayOrUC()`` — demo passthrough unless UC flag set."""
        if m_tx.get("use_urgent_care_copay"):
            return round(copay_amount * 1.15, 2)
        return copay_amount

    def calculate_copay(self, m_tx: dict[str, Any], costs: dict[str, Any]) -> dict[str, Any]:
        """C++: ``calculateCopay()`` — tier + compound + inflation."""
        drug = m_tx.get("drug") or {}
        compound_tiers = [
            int(x["tier"])
            for x in (drug.get("compound_ingredients") or [])
            if isinstance(x, dict) and x.get("tier") is not None
        ]
        # PDF: compound tier mismatch — highest ingredient tier drives copay (demo).
        tier = max(compound_tiers) if compound_tiers else int(drug.get("tier") or 2)
        base = float(self._tier_copays.get(tier, 35.0))
        # Cap copay at total cost basis (demo sanity).
        cap = costs.get("total_cost_basis") or base
        base = min(base, float(cap)) if cap else base
        comp = self.handle_compound_drugs(m_tx, base)
        inflated = self.inflate_to_copay_or_uc(m_tx, comp["copay_amount"])
        return {
            "copay_amount": inflated,
            "tier": tier,
            "selected_tier": tier,
            "compound": comp.get("compound", False),
            "total_cost_basis_at_copay": costs.get("total_cost_basis"),
        }


# ---------------------------------------------------------------------------
# MedicareDProcessor
# ---------------------------------------------------------------------------


@dataclass
class MedicareDProcessor:
    """calcClaim2 MedicareDProcessor (~290 lines in C++)."""

    def calculate_tr_oop(self, m_tx: dict[str, Any]) -> float:
        """C++: ``calculateTrOOP()`` — true out-of-pocket accumulator (demo field)."""
        b = m_tx.get("benefits") or {}
        return float(b.get("true_out_of_pocket") or b.get("tr_oop") or 0)

    def handle_coverage_gap(self, m_tx: dict[str, Any], tr_oop: float) -> dict[str, Any]:
        """C++: ``handleCoverageGap()`` — donut hole demo thresholds."""
        # Simplified CMS-like staging for demo only.
        initial_limit = 5030.0
        catastrophic_threshold = 9000.0
        in_gap = initial_limit < tr_oop < catastrophic_threshold
        in_catastrophic = tr_oop >= catastrophic_threshold
        patient_pct = 0.25 if in_gap else (0.05 if in_catastrophic else None)
        return {
            "in_coverage_gap": in_gap,
            "in_catastrophic": in_catastrophic,
            "patient_pay_percentage": patient_pct,
        }

    def process_medicare_d(
        self, m_tx: dict[str, Any], copay_result: dict[str, Any]
    ) -> dict[str, Any]:
        """C++: ``processMedicareD()`` — Part D adjustments when plan is PDP."""
        member = m_tx.get("member") or {}
        plan = member.get("plan") or {}
        plan_id = str(plan.get("plan_id") or "")
        is_part_d = (
            "MEDV" in plan_id
            or "PART_D" in plan_id.upper()
            or m_tx.get("plan_benefit_type") == "medicare_part_d"
        )
        if not is_part_d:
            return {"is_part_d": False, "applied": False}

        tr_oop = self.calculate_tr_oop(m_tx)
        gap = self.handle_coverage_gap(m_tx, tr_oop)
        adj_copay = copay_result.get("copay_amount", 0.0)
        if gap["in_coverage_gap"] and gap["patient_pay_percentage"] is not None:
            # Demo: bump patient share during gap.
            adj_copay = round(float(adj_copay) * (1.0 + gap["patient_pay_percentage"]), 2)
        elif gap["in_catastrophic"]:
            adj_copay = round(min(float(adj_copay), 15.0), 2)

        if gap["in_coverage_gap"]:
            plan_pay_pct = 0.0
        elif gap["in_catastrophic"]:
            plan_pay_pct = 0.95
        else:
            plan_pay_pct = None
        return {
            "is_part_d": True,
            "applied": True,
            "true_out_of_pocket": tr_oop,
            **gap,
            "adjusted_copay": adj_copay,
            "plan_pay_percentage": plan_pay_pct,
        }


# ---------------------------------------------------------------------------
# MarginProcessor (MBA)
# ---------------------------------------------------------------------------


@dataclass
class MarginProcessor:
    """calcClaim2 MarginProcessor (~380 lines in C++)."""

    def backup_mtx_for_mba(self, m_tx: dict[str, Any]) -> dict[str, Any]:
        """C++: ``backupMtxForMBA()`` — snapshot fields before MBA recursion (demo)."""
        pricing = m_tx.get("pricing") or {}
        return {
            "ingredient_cost": pricing.get("ingredient_cost"),
            "plan_pay_hint": pricing.get("plan_pay"),
        }

    def apply_margin_limits(self, raw_margin: float, m_tx: dict[str, Any]) -> float:
        """C++: ``applyMarginLimits()`` — cap MBA dollars."""
        cap = float((m_tx.get("benefits") or {}).get("mba_margin_cap") or 75.0)
        return round(min(max(raw_margin, 0.0), cap), 2)

    def process_margin(
        self, m_tx: dict[str, Any], costs: dict[str, Any], copay_after_part_d: float, loop_type: int = 0
    ) -> dict[str, Any]:
        """C++: ``processMargin()`` — margin-based adjudication slice (demo)."""
        backup = self.backup_mtx_for_mba(m_tx)
        total_cost = float(costs.get("total_cost_basis") or 0)
        prelim_plan = max(0.0, total_cost - float(copay_after_part_d))
        rate = float((m_tx.get("benefits") or {}).get("mba_rate") or 0.02)
        raw = prelim_plan * rate
        margin_applied = self.apply_margin_limits(raw, m_tx)
        plan_after = round(max(0.0, prelim_plan - margin_applied), 2)
        return {
            "mba_margin_applied": margin_applied,
            "plan_pay_after_margin": plan_after,
            "prelim_plan_pay": round(prelim_plan, 2),
            "backup": backup,
            "loop_type": loop_type,
        }

    def process_margin_with_recursion(
        self,
        m_tx: dict[str, Any],
        costs: dict[str, Any],
        copay_after_part_d: float,
    ) -> dict[str, Any]:
        """C++: ``processMarginWithRecursion()`` — MBA max depth; return code 3 when limit hit (demo)."""
        b = m_tx.get("benefits") or {}
        cur = int(b.get("mba_current_recursion", 0))
        max_r = int(b.get("mba_max_recursion", 99))
        base = self.process_margin(m_tx, costs, copay_after_part_d, loop_type=cur)
        if cur >= max_r:
            return {
                **base,
                "return_code": 3,
                "recursion_limit_reached": True,
                "mba_margin_applied": 0.0,
                "plan_pay_after_margin": base.get("prelim_plan_pay", 0),
            }
        return {**base, "return_code": 0, "recursion_limit_reached": False}


# ---------------------------------------------------------------------------
# DeductibleCapProcessor
# ---------------------------------------------------------------------------


@dataclass
class DeductibleCapProcessor:
    """calcClaim2 DeductibleCapProcessor (~410 lines in C++)."""

    def calc_cap_left_sdc(self, m_tx: dict[str, Any]) -> Optional[float]:
        """C++: ``calcCapLeftSDC()`` — remaining benefit / SDC cap (demo)."""
        b = m_tx.get("benefits") or {}
        v = b.get("sdc_cap_remaining")
        return float(v) if v is not None else None

    def apply_cap_limits(self, plan_pay: float, cap_remaining: Optional[float]) -> dict[str, Any]:
        """C++: ``applyCapLimits()`` — trim plan liability to cap."""
        if cap_remaining is None:
            return {"plan_pay_capped": plan_pay, "cap_applied": 0.0}
        capped = min(plan_pay, max(cap_remaining, 0.0))
        return {
            "plan_pay_capped": round(capped, 2),
            "cap_applied": round(plan_pay - capped, 2),
        }

    def process_deductible_and_caps(
        self, m_tx: dict[str, Any], copay: float, margin_result: dict[str, Any]
    ) -> dict[str, Any]:
        """C++: ``processDeductibleAndCaps()``."""
        member = m_tx.get("member") or {}
        ded_rem = member.get("deductible_remaining")
        ded_rem_f = float(ded_rem) if ded_rem is not None else None

        plan_pay = float(margin_result.get("plan_pay_after_margin") or 0)
        patient_from_ded = 0.0
        if ded_rem_f is not None and ded_rem_f > 0 and copay < ded_rem_f:
            patient_from_ded = min(ded_rem_f, max(0.0, float(copay)))

        cap_left = self.calc_cap_left_sdc(m_tx)
        cap_out = self.apply_cap_limits(plan_pay, cap_left)

        return {
            "deductible_remaining_before": ded_rem_f,
            "deductible_applied_demo": round(patient_from_ded, 2),
            "plan_pay_after_caps": cap_out["plan_pay_capped"],
            "benefit_cap_trim": cap_out["cap_applied"],
            "sdc_cap_remaining": cap_left,
        }

    def process_family_deductible(self, m_tx: dict[str, Any], claim_amount: float) -> dict[str, Any]:
        """C++ family deductible aggregation (PDF: FamilyDeductible_PartialApplication)."""
        b = m_tx.get("benefits") or {}
        ind_met = float(b.get("individual_deductible_met", 0))
        ind_limit = float(b.get("individual_deductible_limit", 500))
        ind_rem = max(0.0, ind_limit - ind_met)
        deductible_applied = min(float(claim_amount), ind_rem)
        patient_pay = deductible_applied
        individual_satisfied = ind_rem > 0 and deductible_applied >= ind_rem - 1e-9
        return {
            "deductible_applied": round(deductible_applied, 2),
            "patient_pay": round(patient_pay, 2),
            "individual_deductible_satisfied": individual_satisfied,
            "individual_remaining_after": round(max(0.0, ind_rem - deductible_applied), 2),
        }


# ---------------------------------------------------------------------------
# SpecialProcessor
# ---------------------------------------------------------------------------


@dataclass
class SpecialProcessor:
    """calcClaim2 SpecialProcessor (~770 lines in C++)."""

    def calculate_daw(self, m_tx: dict[str, Any]) -> dict[str, Any]:
        """C++: ``calculateDAW()`` — dispense-as-written surcharge (demo)."""
        drug = m_tx.get("drug") or {}
        daw = str(m_tx.get("dispense_as_written") or drug.get("daw_code") or "0")
        brand_premium = 8.0 if daw in ("1", "2") else 0.0
        return {"daw_code": daw, "daw_patient_premium": brand_premium}

    def process_vaccine_admin_fee(self, m_tx: dict[str, Any]) -> float:
        """C++: ``processVaccineAdminFee()``."""
        name = (m_tx.get("drug") or {}).get("name") or ""
        gpi = str((m_tx.get("drug") or {}).get("gpi") or "")
        if "vaccine" in name.lower() or gpi.startswith("171"):
            return float(m_tx.get("vaccine_admin_fee") or 20.0)
        return 0.0

    def process_special_cases(self, m_tx: dict[str, Any], running_totals: dict[str, Any]) -> dict[str, Any]:
        """C++: ``processSpecialCases()`` — DAW, tax, vaccine, misc."""
        daw = self.calculate_daw(m_tx)
        v_fee = self.process_vaccine_admin_fee(m_tx)
        pharmacy = m_tx.get("pharmacy") or {}
        state = str(pharmacy.get("state") or "")
        base = float(running_totals.get("total_cost_basis") or 0)
        # PDF: multi-state tax scenarios — IL demo rate; TX/MN 0 for Rx demo.
        tax = 0.0
        if state == "IL":
            tax = round(base * 0.0625, 2)
        elif state == "MN":
            tax = round(base * 0.0, 2)
        extra_patient = daw["daw_patient_premium"] + v_fee + tax
        return {
            **daw,
            "vaccine_admin_fee": v_fee,
            "tax_demo": tax,
            "extra_patient_amount": round(extra_patient, 2),
        }


# ---------------------------------------------------------------------------
# ClaimCalculationOrchestrator
# ---------------------------------------------------------------------------


@dataclass
class ClaimCalculationOrchestrator:
    """calcClaim2 ClaimCalculationOrchestrator (~285 lines in C++)."""

    cost_core: CostCalculationCore = field(default_factory=CostCalculationCore)
    copay_calc: CopayCalculator = field(default_factory=CopayCalculator)
    medicare_d: MedicareDProcessor = field(default_factory=MedicareDProcessor)
    margin_proc: MarginProcessor = field(default_factory=MarginProcessor)
    ded_cap: DeductibleCapProcessor = field(default_factory=DeductibleCapProcessor)
    special_proc: SpecialProcessor = field(default_factory=SpecialProcessor)

    def orchestrate_calculation(self, m_tx: dict[str, Any]) -> dict[str, Any]:
        """Run all components in order; same sequence as calcClaim2 C++ orchestrator."""
        ctx: dict[str, Any] = {"return_code": 0, "error_message": None}
        try:
            costs = self.cost_core.calculate_basic_costs(m_tx, loop_type=0)
            ctx["cost"] = costs
            copay = self.copay_calc.calculate_copay(m_tx, costs)
            ctx["copay"] = copay
            md = self.medicare_d.process_medicare_d(m_tx, copay)
            ctx["medicare_d"] = md
            copay_after = float(md["adjusted_copay"]) if md.get("applied") else float(copay["copay_amount"])
            margin = self.margin_proc.process_margin(m_tx, costs, copay_after, loop_type=0)
            ctx["margin"] = margin
            ded = self.ded_cap.process_deductible_and_caps(m_tx, copay_after, margin)
            ctx["deductible_cap"] = ded
            special = self.special_proc.process_special_cases(
                m_tx,
                {"total_cost_basis": costs.get("total_cost_basis"), "copay": copay_after},
            )
            ctx["special"] = special
            val = self.validate_results(ctx, m_tx)
            ctx["orchestrator"] = val
            ctx["return_code"] = val.get("return_code", 0)
        except Exception as exc:  # noqa: BLE001 — demo boundary; mirrors C++ catch → code 6
            ctx["return_code"] = 6
            ctx["error_message"] = str(exc)
        return ctx

    def validate_results(self, ctx: dict[str, Any], m_tx: dict[str, Any]) -> dict[str, Any]:
        """C++: ``validateResults()`` / ``finalizeCalculations`` — demo sanity checks."""
        cost = ctx.get("cost") or {}
        copay_raw = ctx.get("copay") or {}
        copay_amt = float(copay_raw.get("copay_amount") or 0)
        md = ctx.get("medicare_d") or {}
        if md.get("applied"):
            copay_amt = float(md.get("adjusted_copay") or copay_amt)
        margin = ctx.get("margin") or {}
        ded = ctx.get("deductible_cap") or {}
        spec = ctx.get("special") or {}

        plan = float(ded.get("plan_pay_after_caps") or margin.get("plan_pay_after_margin") or 0)
        total_basis = float(cost.get("total_cost_basis") or 0)
        extra = float(spec.get("extra_patient_amount") or 0)
        patient = copay_amt + extra
        sane = total_basis >= 0 and copay_amt >= 0 and plan >= 0 and extra >= 0
        rc = 0 if sane else 6
        return {
            "validated": rc == 0,
            "return_code": rc,
            "patient_pay_demo": round(patient, 2),
            "plan_pay_demo": round(plan, 2),
            "total_cost_basis": total_basis,
        }


def merge_stage(ctx: dict[str, Any], key: str, value: dict[str, Any]) -> dict[str, Any]:
    """Return new context dict with stage merged (immutable update)."""
    out = dict(ctx)
    out[key] = value
    return out
