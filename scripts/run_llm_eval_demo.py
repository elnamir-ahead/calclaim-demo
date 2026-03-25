#!/usr/bin/env python3
"""
Local LLM / output evaluation demo — no LangSmith API required.

Runs deterministic evaluators from ``langsmith_config`` on sample strings and dicts.
For cloud evaluation: upload a **Dataset** in LangSmith and attach **Online Evaluators**
(rubric, pairwise, LLM-as-judge) to your project; use this repo's trace metadata.

Usage:
  python3 scripts/run_llm_eval_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.langsmith_config import (
    evaluate_adjudication_accuracy,
    evaluate_adjudication_schema,
    evaluate_financial_sanity,
    evaluate_hallucination_risk,
    evaluate_pii_leakage,
)


def main() -> None:
    samples = [
        ("Hallucination / hedging", evaluate_hallucination_risk("I believe the copay might be around $10", "")),
        ("PII leak", evaluate_pii_leakage("Approved. Member reachable at test@example.com")),
        ("PII clean", evaluate_pii_leakage("Approved for tier 2 generic.")),
        ("Status match", evaluate_adjudication_accuracy("The claim is approved for payment", "approved")),
        ("Schema OK", evaluate_adjudication_schema(_good_payload())),
        ("Schema bad", evaluate_adjudication_schema({"status": "approved"})),
        ("Money OK", evaluate_financial_sanity({"copay": 10.0, "plan_pay": 40.0})),
        ("Money bad", evaluate_financial_sanity({"copay": -1, "plan_pay": 40.0})),
    ]
    for title, er in samples:
        print(f"{title}: {er.key} score={er.score:.2f} — {er.comment}")


def _good_payload() -> dict:
    return {
        "status": "approved",
        "reject_code": None,
        "reject_reason": None,
        "copay": 10.0,
        "plan_pay": 90.0,
        "dur_alerts": [],
        "reasoning": "Tier 1 generic on formulary.",
        "confidence": 0.92,
    }


if __name__ == "__main__":
    main()
