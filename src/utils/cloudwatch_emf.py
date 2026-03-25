"""
CloudWatch Embedded Metric Format (EMF) — first-class metrics from Lambda logs.

No extra API calls: a single JSON stdout line is parsed by CloudWatch into
``CalcClaim/Workflow`` metrics. Safe for HIPAA-style logging: dimensions are
outcome buckets only (no PHI).

Enable with ENABLE_CLOUDWATCH_EMF=true (set in Terraform/CDK for prod Lambda).
https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_NAMESPACE = "CalcClaim/Workflow"


def emf_enabled() -> bool:
    return os.getenv("ENABLE_CLOUDWATCH_EMF", "").lower() in ("1", "true", "yes")


def emit_adjudication_emf(final_response: dict[str, Any]) -> None:
    """
    Emit one EMF object for adjudication completion (Count metrics + dimensions).

    ``final_response`` should be the API ``result`` object (status, guardrail flags, etc.).
    """
    if not emf_enabled():
        return
    try:
        status = str(final_response.get("status") or "unknown").lower()[:48]
        guard = bool(final_response.get("guardrail_intervened"))
        ac = bool(final_response.get("agentcore_used"))
        mcp = final_response.get("mcp_tool_results") is not None

        ts = int(time.time() * 1000)
        emf = {
            "_aws": {
                "Timestamp": ts,
                "CloudWatchMetrics": [
                    {
                        "Namespace": _NAMESPACE,
                        "Dimensions": [["Outcome", "Guardrail", "AgentCore", "McpEnriched"]],
                        "Metrics": [
                            {"Name": "AdjudicationsCompleted", "Unit": "Count"},
                        ],
                    }
                ],
            },
            "Outcome": status,
            "Guardrail": "yes" if guard else "no",
            "AgentCore": "yes" if ac else "no",
            "McpEnriched": "yes" if mcp else "no",
            "AdjudicationsCompleted": 1,
        }
        # Single line — CloudWatch Logs subscription extracts metrics
        print(json.dumps(emf))
    except Exception as exc:
        logger.debug("EMF emit skipped: %s", exc)
