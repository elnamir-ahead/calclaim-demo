#!/usr/bin/env python3
"""
Local CalcClaim demo runner.

Runs the full LangGraph workflow locally with fake data.
Does NOT require AWS credentials (uses Bedrock mock when not configured).

Usage:
  python3 scripts/run_demo.py                      # run 3 demo claims
  python3 scripts/run_demo.py --n 10               # run 10 demo claims
  python3 scripts/run_demo.py --scenario reversal  # test reversal HITL
  python3 scripts/run_demo.py --generate-data      # just generate and save fake data
  python3 scripts/run_demo.py --report             # write HTML report (minimal console)
  python3 scripts/run_demo.py --report path/to/run.html
  python3 scripts/run_demo.py --report path/to/run.md   # Markdown instead
"""

import sys
import os
import json
import warnings
import asyncio
import argparse
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

# Add project root (for `src`) and this directory (for `run_report`) to path
_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
if str(_SCRIPTS) not in sys.path:
    sys.path.append(str(_SCRIPTS))

from dotenv import load_dotenv

load_dotenv()

# Before langsmith/langchain are imported, align tracing with API key (avoids 403 on bad keys)
from src.utils.env_bootstrap import bootstrap_langchain_env

bootstrap_langchain_env()

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.json import JSON
from rich import print as rprint

from src.data.fake_data import generate_demo_dataset
from src.utils.langsmith_config import configure_tracing
from src.governance import get_audit_logger

from run_report import build_html_report, build_markdown_report, default_report_path

console = Console()


# ---------------------------------------------------------------------------
# Demo helpers
# ---------------------------------------------------------------------------

def print_header():
    console.print(Panel(
        "[bold cyan]Navitus CalcClaim — Enterprise Agentic AI Demo[/bold cyan]\n"
        "[dim]LangGraph + Amazon Bedrock + AgentCore + LangSmith + Governance[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))


def print_claim_summary(claim: dict) -> None:
    table = Table(title=f"Claim: {claim['claim_id']}", border_style="blue")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Drug", claim["drug"]["name"])
    table.add_row("Tier", str(claim["drug"]["tier"]))
    table.add_row("Brand", "Yes" if claim["drug"]["brand"] else "No")
    table.add_row("Status (raw)", claim["status"])
    table.add_row("Cost", f"${claim['drug']['cost']:.2f}")
    table.add_row("PA Required", str(claim["drug"].get("requires_pa", False)))
    table.add_row("Plan", claim["member"]["plan"]["name"])
    console.print(table)


def print_workflow_result(result: dict) -> None:
    status_color = {
        "approved": "green",
        "approved_with_pa": "green",
        "rejected": "red",
        "rejected_pending_pa": "yellow",
        "rejected_refill_too_soon": "yellow",
        "rejected_dur": "red",
        "pending_review": "yellow",
        "denied": "red",
        "reversed": "magenta",
        "unknown": "dim",
    }.get(result.get("status", "unknown"), "white")

    steps = result.get("workflow_steps") or []
    steps_line = " → ".join(steps) if steps else "—"
    deny_note = ""
    if result.get("status") == "denied":
        deny_note = (
            f"\nPolicy: {result.get('policy_id') or '—'}  |  "
            f"{result.get('reason') or '—'}"
        )

    console.print(Panel(
        f"[bold {status_color}]OUTCOME: {result.get('status', 'UNKNOWN').upper()}[/bold {status_color}]\n"
        f"Copay: ${result.get('pricing', {}).get('copay', 0):.2f}  |  "
        f"Plan Pay: ${result.get('pricing', {}).get('plan_pay', 0):.2f}\n"
        f"Reject Code: {result.get('reject_code') or '—'}  |  "
        f"HITL: {result.get('hitl_resolution') or '—'}\n"
        f"Guardrail: {result.get('guardrail_intervened', False)}\n"
        f"AgentCore: {'yes' if result.get('agentcore_used') else 'no'}"
        f"{(' (' + str(result.get('agentcore_ms')) + ' ms)') if result.get('agentcore_ms') is not None else ''}\n"
        f"Workflow Steps: {steps_line}{deny_note}\n"
        f"Audit Events: {len(result.get('audit_trail', []))}",
        title="Adjudication Result",
        border_style=status_color,
    ))


async def run_claim(claim: dict, action: str = "adjudicate") -> dict:
    """Run a single claim through the LangGraph workflow."""
    from src.graph.claims_workflow import compile_claims_graph
    from src.utils.launchdarkly_flags import evaluate_calclaim_flags

    graph = compile_claims_graph()

    state = {
        "claim_id": claim["claim_id"],
        "session_id": f"demo-{claim['claim_id']}",
        "raw_claim": claim,
        "actor_id": "demo-runner",
        "actor_role": "claims_processor",
        "action": action,
        "messages": [],
        "workflow_steps": [],
        "errors": [],
        "audit_event_ids": [],
        "feature_flags": evaluate_calclaim_flags("demo-runner"),
    }

    result_state = await graph.ainvoke(state)
    return result_state.get("final_response", {})


async def run_demo(
    n: int = 3,
    scenario: str = "mixed",
    *,
    report_path: Optional[Path] = None,
) -> None:
    configure_tracing()
    quiet = report_path is not None
    if quiet:
        warnings.filterwarnings("ignore", category=DeprecationWarning)
    if not quiet:
        print_header()

    if not quiet:
        console.print(f"\n[dim]Generating {n} demo claims (scenario: {scenario})...[/dim]")
    dataset = generate_demo_dataset(n_members=n, claims_per_member=1)
    claims = dataset["claims"]

    if not quiet:
        console.print(f"[green]✓[/green] Generated {len(claims)} claims | "
                      f"Total plan liability: ${dataset['summary']['total_plan_liability']:,.2f}\n")

    results = []
    for i, claim in enumerate(claims, 1):
        if not quiet:
            console.rule(f"[bold]Claim {i}/{len(claims)}[/bold]")
            print_claim_summary(claim)

        action = "reverse" if scenario == "reversal" and i == 1 else "adjudicate"

        if not quiet:
            console.print(f"\n[dim]▶ Running CalcClaim workflow (action={action})...[/dim]")
        try:
            result = await run_claim(claim, action=action)
            if not quiet:
                print_workflow_result(result)
            results.append({"claim_id": claim["claim_id"], "success": True, "result": result})
        except Exception as exc:
            if not quiet:
                console.print(f"[red]✗ Error: {exc}[/red]")
            results.append({"claim_id": claim["claim_id"], "success": False, "error": str(exc)})

        if not quiet:
            console.print()

    # Summary
    if not quiet:
        console.rule("[bold cyan]Demo Summary[/bold cyan]")
        summary_table = Table(border_style="cyan")
        summary_table.add_column("Metric", style="bold")
        summary_table.add_column("Value", justify="right")
        summary_table.add_row("Claims processed", str(len(results)))
        summary_table.add_row("Successful", str(sum(1 for r in results if r["success"])))
        approved = sum(1 for r in results if r.get("result", {}).get("status") in ("approved", "approved_with_pa"))
        summary_table.add_row("Approved", f"[green]{approved}[/green]")
        rejected = sum(1 for r in results if "rejected" in r.get("result", {}).get("status", ""))
        summary_table.add_row("Rejected", f"[red]{rejected}[/red]")
        hitl = sum(1 for r in results if r.get("result", {}).get("hitl_resolution"))
        summary_table.add_row("HITL triggered", f"[yellow]{hitl}[/yellow]")
        console.print(summary_table)

    # Audit log dump
    audit = get_audit_logger()
    all_events = audit.dump_memory_log()
    if not quiet:
        console.print(f"\n[dim]Audit log: {len(all_events)} events recorded[/dim]")

    run_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    output_path = Path("demo_output.json")
    with open(output_path, "w") as f:
        json.dump({
            "run_at": run_at,
            "results": results,
            "audit_log": all_events,
        }, f, indent=2)
    if not quiet:
        console.print(f"[green]✓[/green] Results saved to [bold]{output_path}[/bold]")

    if quiet:
        rp = report_path.resolve()
        suffix = rp.suffix.lower()
        if suffix == ".md":
            use_md = True
        elif suffix in (".html", ".htm"):
            use_md = False
        else:
            rp = rp.with_suffix(".html")
            use_md = False
        rp.parent.mkdir(parents=True, exist_ok=True)
        kw = dict(
            run_at_utc=run_at,
            scenario=scenario,
            dataset_summary=dict(dataset.get("summary") or {}),
            results=results,
            audit_events=all_events,
            json_artifact=str(output_path.resolve()),
            claims=claims,
        )
        body = build_markdown_report(**kw) if use_md else build_html_report(**kw)
        rp.write_text(body, encoding="utf-8")
        print(f"Wrote report: {rp}")
        print(f"Wrote JSON:   {output_path.resolve()}")


def generate_data_only(n: int = 50) -> None:
    dataset = generate_demo_dataset(n_members=n, claims_per_member=3)
    out = Path("src/data/sample_claims.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(dataset, f, indent=2)
    console.print(f"[green]✓[/green] Generated {len(dataset['claims'])} claims → {out}")
    console.print(JSON(json.dumps(dataset["summary"])))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CalcClaim Demo Runner")
    parser.add_argument("--n", type=int, default=3, help="Number of demo claims")
    parser.add_argument("--scenario", choices=["mixed", "reversal", "pa", "dur"], default="mixed")
    parser.add_argument("--generate-data", action="store_true", help="Only generate fake data")
    parser.add_argument(
        "--report",
        nargs="?",
        const="__DEFAULT__",
        default=None,
        metavar="PATH",
        help="Write HTML report to PATH (default: reports/calclaim-run-<UTC>.html); use .md for Markdown; suppress Rich UI",
    )
    args = parser.parse_args()

    if args.generate_data:
        generate_data_only()
    else:
        rpath: Optional[Path] = None
        if args.report is not None:
            rpath = default_report_path() if args.report == "__DEFAULT__" else Path(args.report)
        asyncio.run(run_demo(n=args.n, scenario=args.scenario, report_path=rpath))


if __name__ == "__main__":
    main()
