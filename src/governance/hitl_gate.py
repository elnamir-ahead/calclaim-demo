"""
Human-in-the-Loop (HITL) Gate — mirrors 'HITL Gates (PHI detect, Bulk, Destructive)'.

Triggers:
  - PHI detected in agent output
  - Bulk operation threshold breached
  - Destructive action (reversal, override)
  - Tier-5 specialty drug / high-value claim

In AWS: sends to SNS → SQS → reviewer UI.
In demo: simulates auto-approval after a brief hold.
"""

from __future__ import annotations

import os
import json
import uuid
import logging
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
HITL_SNS_TOPIC = os.getenv("HITL_SNS_TOPIC_ARN", "")

HITLTriggerType = Literal["PHI_DETECTED", "BULK_OPERATION", "DESTRUCTIVE_ACTION",
                           "HIGH_VALUE_CLAIM", "TIER5_DRUG", "POLICY_DENY"]
HITLResolution = Literal["APPROVED", "DENIED", "ESCALATED", "PENDING"]


@dataclass
class HITLRequest:
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trigger_type: HITLTriggerType = "POLICY_DENY"
    claim_id: str = ""
    session_id: str = ""
    reason: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolution: HITLResolution = "PENDING"
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__


class HITLGate:
    """
    Routes workflow events that require human review.
    Demo mode: auto-approves low-risk, auto-denies PHI leaks.
    """

    def __init__(self) -> None:
        self._pending: dict[str, HITLRequest] = {}
        self._sns: Any = None
        if not DEMO_MODE and HITL_SNS_TOPIC:
            import boto3
            self._sns = boto3.client("sns")

    # ------------------------------------------------------------------
    # Trigger
    # ------------------------------------------------------------------

    def trigger(
        self,
        trigger_type: HITLTriggerType,
        claim_id: str,
        reason: str,
        context: Optional[dict] = None,
        session_id: str = "",
    ) -> HITLRequest:
        req = HITLRequest(
            trigger_type=trigger_type,
            claim_id=claim_id,
            session_id=session_id,
            reason=reason,
            context=context or {},
        )
        self._pending[req.request_id] = req
        logger.warning(
            "HITL TRIGGERED | type=%s | claim=%s | reason=%s | request_id=%s",
            trigger_type, claim_id, reason, req.request_id,
        )

        if DEMO_MODE:
            self._demo_auto_resolve(req)
        else:
            self._publish_sns(req)

        return req

    # ------------------------------------------------------------------
    # Demo auto-resolution (simulates reviewer decision)
    # ------------------------------------------------------------------

    def _demo_auto_resolve(self, req: HITLRequest) -> None:
        if req.trigger_type == "PHI_DETECTED":
            self._resolve(req.request_id, "DENIED", "system-demo",
                          "Auto-denied: PHI leakage detected in output")
        elif req.trigger_type == "DESTRUCTIVE_ACTION":
            self._resolve(req.request_id, "APPROVED", "system-demo",
                          "Auto-approved in demo mode — dual approval simulated")
        else:
            self._resolve(req.request_id, "APPROVED", "system-demo",
                          "Auto-approved in demo mode — human review simulated")

    # ------------------------------------------------------------------
    # AWS SNS publish (production)
    # ------------------------------------------------------------------

    def _publish_sns(self, req: HITLRequest) -> None:
        try:
            self._sns.publish(
                TopicArn=HITL_SNS_TOPIC,
                Subject=f"HITL Review Required: {req.trigger_type} | Claim {req.claim_id}",
                Message=json.dumps(req.to_dict()),
                MessageAttributes={
                    "trigger_type": {"DataType": "String", "StringValue": req.trigger_type},
                    "claim_id": {"DataType": "String", "StringValue": req.claim_id},
                },
            )
            logger.info("HITL SNS published: request_id=%s", req.request_id)
        except Exception as exc:
            logger.error("HITL SNS publish failed: %s", exc)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def _resolve(
        self,
        request_id: str,
        resolution: HITLResolution,
        resolved_by: str,
        note: str = "",
    ) -> Optional[HITLRequest]:
        req = self._pending.get(request_id)
        if not req:
            logger.warning("HITL resolve: unknown request_id=%s", request_id)
            return None
        req.resolution = resolution
        req.resolved_at = datetime.now(timezone.utc).isoformat()
        req.resolved_by = resolved_by
        req.resolution_note = note
        logger.info(
            "HITL RESOLVED | request_id=%s | resolution=%s | by=%s",
            request_id, resolution, resolved_by,
        )
        return req

    def resolve(
        self,
        request_id: str,
        resolution: HITLResolution,
        resolved_by: str,
        note: str = "",
    ) -> Optional[HITLRequest]:
        return self._resolve(request_id, resolution, resolved_by, note)

    def get_pending(self) -> list[HITLRequest]:
        return [r for r in self._pending.values() if r.resolution == "PENDING"]

    def get_request(self, request_id: str) -> Optional[HITLRequest]:
        return self._pending.get(request_id)


_hitl_gate: Optional[HITLGate] = None


def get_hitl_gate() -> HITLGate:
    global _hitl_gate
    if _hitl_gate is None:
        _hitl_gate = HITLGate()
    return _hitl_gate
