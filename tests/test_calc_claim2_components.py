"""
calcClaim2 component tests aligned with the **calcClaim vs calcClaim2** PDF testing strategy.

The PDF contrasts monolithic calcClaim integration tests with **focused unit tests per
component** (CostCalculationCore, CopayCalculator, MedicareDProcessor, MarginProcessor,
DeductibleCapProcessor, SpecialProcessor, ClaimCalculationOrchestrator), plus categories
such as multi-source pricing, coverage gap, family deductible, DAW, vaccine fees, MBA
recursion, and orchestrator failure recovery.

This module maps those expectations onto the **Python demo** in ``calc_claim2_components.py``
(not the production C++ suite). Test class docstrings reference the PDF section intent.
"""

from __future__ import annotations

import pytest

from src.graph.calc_claim2_components import (
    ClaimCalculationOrchestrator,
    CopayCalculator,
    CostCalculationCore,
    DeductibleCapProcessor,
    MarginProcessor,
    MedicareDProcessor,
    SpecialProcessor,
)


# --- CostCalculationCore (PDF: Multi-source pricing, MAC/AWP/WAC, calculateBasicCosts) ---


class TestCostCalculationCorePDF:
    """PDF § Code Coverage / CostCalculationCore — multi-source pricing, lesser-of, MAC."""

    def test_multi_source_pricing_mac_lowest(self):
        """PDF: MultiSourcePricingConflict — MAC wins when lowest."""
        core = CostCalculationCore()
        m_tx = {
            "pricing": {"ingredient_cost": 100.0, "awp": 100.0, "mac": 75.0, "wac": 85.0, "dispensing_fee": 2.0},
        }
        mac_info = core.calculate_mac_pricing(m_tx)
        lesser = core.apply_lesser_of_logic(mac_info)
        assert lesser["selected_price"] == 75.0
        assert lesser["price_source"] == "MAC"

    def test_calculate_basic_costs_includes_dispensing_fee(self):
        """PDF: CalculateAWPCosts-style — ingredient + dispensing in basis."""
        core = CostCalculationCore()
        m_tx = {
            "pricing": {"ingredient_cost": 50.0, "awp": 60.0, "dispensing_fee": 2.75},
        }
        r = core.calculate_basic_costs(m_tx)
        assert r["dispensing_fee"] == 2.75
        assert r["total_cost_basis"] == pytest.approx(r["ingredient_cost_payable"] + 2.75)

    def test_derived_mac_when_absent(self):
        """MAC derived from AWP when not on claim (demo 0.88 factor)."""
        core = CostCalculationCore()
        m_tx = {"pricing": {"ingredient_cost": 100.0, "awp": 100.0, "dispensing_fee": 0.0}}
        mp = core.calculate_mac_pricing(m_tx)
        assert mp["mac"] == pytest.approx(88.0, rel=0.01)

    def test_apply_lesser_of_empty_sources(self):
        core = CostCalculationCore()
        assert core.apply_lesser_of_logic({})["price_source"] == "NONE"


# --- CopayCalculator (PDF: compound tier mismatch, inflation, tier copays) ---


class TestCopayCalculatorPDF:
    """PDF § CopayCalculator — compound handling, tier copays, inflateToCopayOrUC."""

    def test_calculate_basic_copay_by_tier(self):
        calc = CopayCalculator()
        costs = {"total_cost_basis": 500.0}
        m_tx = {"drug": {"tier": 1}}
        r = calc.calculate_copay(m_tx, costs)
        assert r["copay_amount"] == 10.0

    def test_compound_tier_mismatch_highest_tier_wins(self):
        """PDF: CompoundDrugTierMismatch — highest ingredient tier drives copay."""
        calc = CopayCalculator()
        costs = {"total_cost_basis": 500.0}
        m_tx = {
            "drug": {
                "tier": 1,
                "compound_ingredients": [{"tier": 1}, {"tier": 3}, {"tier": 4}],
            }
        }
        r = calc.calculate_copay(m_tx, costs)
        assert r["selected_tier"] == 4
        # Tier-4 base $100 + compound uplift (3 ingredients × 5% in demo).
        assert r["copay_amount"] == pytest.approx(115.0)

    def test_compound_uplift_with_ingredient_list(self):
        calc = CopayCalculator()
        costs = {"total_cost_basis": 500.0}
        m_tx = {"drug": {"tier": 2, "compound_ingredients": [{"ndc": "a"}, {"ndc": "b"}]}}
        r = calc.calculate_copay(m_tx, costs)
        assert r["compound"] is True
        assert r["copay_amount"] > 35.0

    def test_inflate_urgent_care_copay(self):
        calc = CopayCalculator()
        costs = {"total_cost_basis": 500.0}
        m_tx = {"drug": {"tier": 2}, "use_urgent_care_copay": True}
        r = calc.calculate_copay(m_tx, costs)
        assert r["copay_amount"] == pytest.approx(35.0 * 1.15)

    def test_copay_capped_by_total_cost_basis(self):
        calc = CopayCalculator()
        costs = {"total_cost_basis": 25.0}
        m_tx = {"drug": {"tier": 5}}
        r = calc.calculate_copay(m_tx, costs)
        assert r["copay_amount"] <= 25.0


# --- MedicareDProcessor (PDF: coverage gap, TrOOP, catastrophic, plan pay %) ---


class TestMedicareDProcessorPDF:
    """PDF § MedicareDProcessor — CoverageGapTransition, TrOOP, catastrophic."""

    def test_coverage_gap_transition_entering_gap(self):
        """PDF: totalDrugCosts / TrOOP — in gap, plan pay 0%, patient share elevated."""
        proc = MedicareDProcessor()
        m_tx = {
            "member": {"plan": {"plan_id": "PLN-MEDV-001"}},
            "benefits": {"true_out_of_pocket": 5100.0},
        }
        cop = {"copay_amount": 35.0}
        r = proc.process_medicare_d(m_tx, cop)
        assert r["is_part_d"] is True
        assert r["in_coverage_gap"] is True
        assert r["plan_pay_percentage"] == 0.0
        assert r["adjusted_copay"] > cop["copay_amount"]

    def test_catastrophic_min_copay(self):
        m_tx = {
            "member": {"plan": {"plan_id": "PLN-MEDV-001"}},
            "benefits": {"true_out_of_pocket": 9500.0},
        }
        r = MedicareDProcessor().process_medicare_d(m_tx, {"copay_amount": 70.0})
        assert r["in_catastrophic"] is True
        assert r["adjusted_copay"] <= 15.0

    def test_not_part_d_skipped(self):
        m_tx = {"member": {"plan": {"plan_id": "PLN-COMM-001"}}}
        r = MedicareDProcessor().process_medicare_d(m_tx, {"copay_amount": 10.0})
        assert r["is_part_d"] is False
        assert r["applied"] is False

    def test_calculate_tr_oop(self):
        assert (
            MedicareDProcessor().calculate_tr_oop({"benefits": {"true_out_of_pocket": 1500.0}})
            == 1500.0
        )

    def test_handle_coverage_gap_below_initial(self):
        gap = MedicareDProcessor().handle_coverage_gap({}, 2000.0)
        assert gap["in_coverage_gap"] is False
        assert gap["in_catastrophic"] is False


# --- MarginProcessor (PDF: MBA, limits, recursion / return code 3) ---


class TestMarginProcessorPDF:
    """PDF § MarginProcessor — processMargin, applyMarginLimits, MBARecursionLimit."""

    def test_process_margin_reduces_plan_pay(self):
        proc = MarginProcessor()
        costs = {"total_cost_basis": 100.0}
        r = proc.process_margin({}, costs, copay_after_part_d=10.0)
        assert r["prelim_plan_pay"] == 90.0
        assert r["plan_pay_after_margin"] < r["prelim_plan_pay"]

    def test_apply_margin_limits_respects_cap(self):
        proc = MarginProcessor()
        m_tx = {"benefits": {"mba_margin_cap": 1.0}}
        assert proc.apply_margin_limits(999.0, m_tx) == 1.0

    def test_backup_mtx_for_mba(self):
        proc = MarginProcessor()
        snap = proc.backup_mtx_for_mba({"pricing": {"ingredient_cost": 12.0, "plan_pay": 5.0}})
        assert snap["ingredient_cost"] == 12.0

    def test_mba_recursion_limit_return_code_3(self):
        """PDF: MBARecursionLimit_MaximumDepth — returnCode 3, recursionLimitReached."""
        proc = MarginProcessor()
        m_tx = {"benefits": {"mba_current_recursion": 5, "mba_max_recursion": 5}}
        costs = {"total_cost_basis": 50.0}
        r = proc.process_margin_with_recursion(m_tx, costs, copay_after_part_d=10.0)
        assert r["recursion_limit_reached"] is True
        assert r["return_code"] == 3


# --- DeductibleCapProcessor (PDF: family deductible, SDC caps, applyCapLimits) ---


class TestDeductibleCapProcessorPDF:
    """PDF § DeductibleCapProcessor — FamilyDeductible_PartialApplication, benefit caps."""

    def test_family_deductible_partial_application(self):
        """PDF: individual $450 met / $500 limit → $50 remaining; claim $200 → apply $50."""
        proc = DeductibleCapProcessor()
        m_tx = {
            "benefits": {
                "individual_deductible_met": 450.0,
                "individual_deductible_limit": 500.0,
            }
        }
        r = proc.process_family_deductible(m_tx, 200.0)
        assert r["deductible_applied"] == 50.0
        assert r["patient_pay"] == 50.0
        assert r["individual_deductible_satisfied"] is True

    def test_apply_cap_limits_trims_plan(self):
        proc = DeductibleCapProcessor()
        out = proc.apply_cap_limits(plan_pay=100.0, cap_remaining=40.0)
        assert out["plan_pay_capped"] == 40.0
        assert out["cap_applied"] == 60.0

    def test_process_deductible_and_caps_with_sdc(self):
        proc = DeductibleCapProcessor()
        m_tx = {"benefits": {"sdc_cap_remaining": 25.0}}
        margin = {"plan_pay_after_margin": 80.0}
        r = proc.process_deductible_and_caps(m_tx, copay=10.0, margin_result=margin)
        assert r["plan_pay_after_caps"] == 25.0
        assert r["benefit_cap_trim"] == 55.0


# --- SpecialProcessor (PDF: DAW, vaccine admin, multi-state tax) ---


class TestSpecialProcessorPDF:
    """PDF § SpecialProcessor — DAW overrides, vaccine fees, multi-state tax."""

    def test_daw_physician_patient_brand_premium(self):
        proc = SpecialProcessor()
        r = proc.calculate_daw({"dispense_as_written": "1"})
        assert r["daw_code"] == "1"
        assert r["daw_patient_premium"] == 8.0

    def test_vaccine_admin_fee_flu_by_name(self):
        proc = SpecialProcessor()
        assert (
            proc.process_vaccine_admin_fee({"drug": {"name": "Influenza vaccine quad"}}) == 20.0
        )

    def test_vaccine_admin_fee_by_gpi_171(self):
        proc = SpecialProcessor()
        assert proc.process_vaccine_admin_fee({"drug": {"gpi": "17100000000000"}}) == 20.0

    def test_tax_il_demo_rate(self):
        proc = SpecialProcessor()
        m_tx = {"pharmacy": {"state": "IL"}, "drug": {}}
        r = proc.process_special_cases(m_tx, {"total_cost_basis": 100.0, "copay": 10.0})
        assert r["tax_demo"] == pytest.approx(6.25)
        assert r["extra_patient_amount"] >= r["tax_demo"]

    def test_tax_tx_zero(self):
        proc = SpecialProcessor()
        m_tx = {"pharmacy": {"state": "TX"}, "drug": {}}
        r = proc.process_special_cases(m_tx, {"total_cost_basis": 100.0, "copay": 10.0})
        assert r["tax_demo"] == 0.0


# --- ClaimCalculationOrchestrator (PDF: validateResults, component failure → return 6) ---


class TestClaimCalculationOrchestratorPDF:
    """PDF § ClaimCalculationOrchestrator — orchestration, validateResults, failure recovery."""

    def test_orchestrate_runs_all_stages_functional_equivalence_shape(self):
        """End state exposes same component keys as C++ result aggregation (demo)."""
        m_tx = {
            "drug": {"tier": 1, "name": "Lisinopril"},
            "member": {"plan": {"plan_id": "PLN-COMM-001"}},
            "pharmacy": {"state": "TX"},
            "pricing": {"ingredient_cost": 12.5, "dispensing_fee": 2.5, "awp": 15.0},
            "quantity_dispensed": 30,
        }
        ctx = ClaimCalculationOrchestrator().orchestrate_calculation(m_tx)
        assert ctx["return_code"] == 0
        for k in ("cost", "copay", "medicare_d", "margin", "deductible_cap", "special", "orchestrator"):
            assert k in ctx
        assert ctx["orchestrator"]["validated"] is True

    def test_validate_results_return_codes_non_negative(self):
        orch = ClaimCalculationOrchestrator()
        ctx = {
            "cost": {"total_cost_basis": 40.0},
            "copay": {"copay_amount": 10.0},
            "medicare_d": {"applied": False},
            "margin": {"plan_pay_after_margin": 30.0},
            "deductible_cap": {"plan_pay_after_caps": 30.0},
            "special": {"extra_patient_amount": 0.0},
        }
        v = orch.validate_results(ctx, {})
        assert v["return_code"] == 0
        assert v["validated"] is True

    def test_component_failure_recovery_return_code_6(self):
        """PDF: ComponentFailureRecovery — cost throws → orchestrate returns 6."""
        m_tx = {
            "pricing": object(),
        }
        ctx = ClaimCalculationOrchestrator().orchestrate_calculation(m_tx)
        assert ctx["return_code"] == 6
        assert ctx.get("error_message")


class TestCalcClaim2TestingStrategyMeta:
    """Documents PDF testing-strategy metrics we mirror in spirit (demo scope)."""

    def test_failure_isolation_per_component_import(self):
        """PDF: failures attributable to a single component under test (import isolation)."""
        from src.graph import calc_claim2_components as m

        assert hasattr(m, "CostCalculationCore")
        assert hasattr(m, "CopayCalculator")
        assert hasattr(m, "MedicareDProcessor")
        assert hasattr(m, "MarginProcessor")
        assert hasattr(m, "DeductibleCapProcessor")
        assert hasattr(m, "SpecialProcessor")
        assert hasattr(m, "ClaimCalculationOrchestrator")
