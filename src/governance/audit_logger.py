"""
Immutable Audit Logger — mirrors 'Audit Record (Immutable CloudTrail, Auto-rollback)'.

Every claim action is written to:
  1. DynamoDB  (primary, queryable, TTL-backed)
  2. S3        (archive — WORM via Object Lock; auto-rollback trigger on anomaly)
  3. CloudWatch Logs (real-time stream for SIEM / Splunk)

In demo mode (no real AWS) all writes go to an in-memory log + stdout.
"""

from __future__ import annotations

import os
import json
import uuid
import hashlib
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
AUDIT_TABLE = os.getenv("DYNAMODB_AUDIT_TABLE", "calclaim-audit-log")
AUDIT_BUCKET = os.getenv("S3_AUDIT_BUCKET", "calclaim-audit-archive")
AUDIT_LOG_GROUP = "/calclaim/audit"

AuditEventType = Literal[
    "CLAIM_RECEIVED",
    "PII_SCRUB",
    "GOVERNANCE_CHECK",
    "HITL_TRIGGERED",
    "HITL_RESOLVED",
    "ADJUDICATION_STARTED",
    "AGENTCORE_INVOKED",
    "MCP_TOOLS_INVOKED",
    "CALC_CLAIM2_STAGE",
    "ADJUDICATION_COMPLETE",
    "POLICY_EVALUATED",
    "GUARDRAIL_TRIGGERED",
    "REVERSAL_INITIATED",
    "REVERSAL_COMPLETE",
    "AUDIT_QUERY",
    "ERROR",
]


class AuditEvent:
    def __init__(
        self,
        event_type: AuditEventType,
        claim_id: str,
        actor: str,
        details: dict[str, Any],
        session_id: str = "",
        outcome: str = "SUCCESS",
    ) -> None:
        self.event_id = str(uuid.uuid4())
        self.event_type = event_type
        self.claim_id = claim_id
        self.actor = actor
        self.details = details
        self.session_id = session_id
        self.outcome = outcome
        self.timestamp_utc = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "claim_id": self.claim_id,
            "actor": self.actor,
            "session_id": self.session_id,
            "outcome": self.outcome,
            "timestamp_utc": self.timestamp_utc,
            "details": self.details,
            "integrity_hash": self._hash(),
        }

    def _hash(self) -> str:
        """SHA-256 of deterministic fields — used to detect tampering."""
        payload = f"{self.event_id}:{self.event_type}:{self.claim_id}:{self.timestamp_utc}"
        return hashlib.sha256(payload.encode()).hexdigest()


def _floats_to_decimal(value: Any) -> Any:
    """DynamoDB does not accept Python float; use Decimal for numeric fields."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _floats_to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_floats_to_decimal(v) for v in value]
    return value


class AuditLogger:
    """
    Writes immutable audit events to DynamoDB + S3 + CloudWatch.
    Falls back to in-memory + stdout in demo/local mode.
    """

    def __init__(self) -> None:
        self._memory_log: list[dict[str, Any]] = []
        self._dynamo: Any = None
        self._s3: Any = None
        self._cw_logs: Any = None
        self._cw_stream: str = ""

        if not DEMO_MODE:
            self._init_aws()

    def _init_aws(self) -> None:
        import boto3
        self._dynamo = boto3.resource("dynamodb").Table(AUDIT_TABLE)
        self._s3 = boto3.client("s3")
        self._cw_logs = boto3.client("logs")
        self._ensure_log_stream()

    def _ensure_log_stream(self) -> None:
        stream_name = f"calclaim/{datetime.utcnow().strftime('%Y/%m/%d')}"

        def _create_stream() -> None:
            try:
                self._cw_logs.create_log_stream(
                    logGroupName=AUDIT_LOG_GROUP,
                    logStreamName=stream_name,
                )
            except self._cw_logs.exceptions.ResourceAlreadyExistsException:
                pass

        try:
            _create_stream()
        except self._cw_logs.exceptions.ResourceNotFoundException:
            # Log group missing (e.g. Terraform not applied) — create if IAM allows.
            try:
                self._cw_logs.create_log_group(logGroupName=AUDIT_LOG_GROUP)
            except self._cw_logs.exceptions.ResourceAlreadyExistsException:
                pass
            _create_stream()
        self._cw_stream = stream_name

    # ------------------------------------------------------------------

    def log(
        self,
        event_type: AuditEventType,
        claim_id: str,
        actor: str = "system",
        details: Optional[dict] = None,
        session_id: str = "",
        outcome: str = "SUCCESS",
    ) -> str:
        event = AuditEvent(
            event_type=event_type,
            claim_id=claim_id,
            actor=actor,
            details=details or {},
            session_id=session_id,
            outcome=outcome,
        )
        record = event.to_dict()

        if DEMO_MODE:
            self._memory_log.append(record)
            logger.info("AUDIT | %s | %s | %s | %s", event_type, claim_id, outcome, actor)
        else:
            self._write_dynamo(record)
            self._write_s3(record)
            self._write_cloudwatch(record)

        return event.event_id

    def _write_dynamo(self, record: dict[str, Any]) -> None:
        try:
            item = _floats_to_decimal(dict(record))
            item["ttl"] = int(datetime.utcnow().timestamp()) + (365 * 86400)
            self._dynamo.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(event_id)",  # immutability guard
            )
        except Exception as exc:
            logger.error("Audit DynamoDB write failed: %s", exc)

    def _write_s3(self, record: dict[str, Any]) -> None:
        try:
            key = f"audit/{record['timestamp_utc'][:10]}/{record['claim_id']}/{record['event_id']}.json"
            self._s3.put_object(
                Bucket=AUDIT_BUCKET,
                Key=key,
                Body=json.dumps(record).encode(),
                ContentType="application/json",
                # Object Lock (WORM) is configured at the bucket level via CDK
            )
        except Exception as exc:
            logger.error("Audit S3 write failed: %s", exc)

    def _write_cloudwatch(self, record: dict[str, Any]) -> None:
        try:
            self._cw_logs.put_log_events(
                logGroupName=AUDIT_LOG_GROUP,
                logStreamName=self._cw_stream,
                logEvents=[{
                    "timestamp": int(datetime.utcnow().timestamp() * 1000),
                    "message": json.dumps(record),
                }],
            )
        except Exception as exc:
            logger.error("Audit CloudWatch write failed: %s", exc)

    def get_claim_trail(self, claim_id: str) -> list[dict[str, Any]]:
        """Retrieve full audit trail for a claim (demo: from memory)."""
        if DEMO_MODE:
            return [e for e in self._memory_log if e["claim_id"] == claim_id]
        try:
            response = self._dynamo.query(
                IndexName="claim_id-index",
                KeyConditionExpression="claim_id = :cid",
                ExpressionAttributeValues={":cid": claim_id},
                ScanIndexForward=True,
            )
            return response.get("Items", [])
        except Exception as exc:
            logger.error("Audit trail query failed: %s", exc)
            return []

    def dump_memory_log(self) -> list[dict[str, Any]]:
        return list(self._memory_log)


_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger
