"""
PII / PHI scrubbing — mirrors the 'PII Scrub (Macie pre-scan, De-identification)' 
box in the Quality & Governance Gate.

Uses Microsoft Presidio for entity recognition + anonymization, with a Bedrock
Guardrail secondary check.  In AWS, the presidio step runs inside Lambda;
the Macie scan is async on S3-bound payloads.
"""

from __future__ import annotations

import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Presidio imports are optional — fall back to regex-only in local dev
try:
    from presidio_analyzer import AnalyzerEngine, RecognizerResult
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig
    _PRESIDIO_AVAILABLE = True
except ImportError:
    _PRESIDIO_AVAILABLE = False
    logger.warning("Presidio not installed — falling back to regex scrubber")


# ---------------------------------------------------------------------------
# Regex patterns (baseline, always active)
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("SSN",   "[SSN-REDACTED]",   re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("SSN4",  "[SSN4-REDACTED]",  re.compile(r"\bSSN last 4[:\s]+\d{4}\b", re.IGNORECASE)),
    ("EMAIL", "[EMAIL-REDACTED]", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("PHONE", "[PHONE-REDACTED]", re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")),
    ("DOB",   "[DOB-REDACTED]",   re.compile(r"\b(19|20)\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b")),
    ("DEA",   "[DEA-REDACTED]",   re.compile(r"\b[A-Z]{2}\d{7}\b")),
    ("NPI",   "[NPI-REDACTED]",   re.compile(r"\bNPI[:\s]+\d{10}\b", re.IGNORECASE)),
    ("CARD",  "[CARD-REDACTED]",  re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")),
]


class PHIScrubber:
    """
    Two-stage scrubber:
      1. Presidio (NLP-based entity recognition)
      2. Regex fallback / supplement
    """

    def __init__(self) -> None:
        self._analyzer: Any = None
        self._anonymizer: Any = None
        if _PRESIDIO_AVAILABLE:
            self._analyzer = AnalyzerEngine()
            self._anonymizer = AnonymizerEngine()

    def scrub_text(self, text: str) -> tuple[str, list[str]]:
        """
        Returns (scrubbed_text, list_of_entity_types_found).
        """
        found_entities: list[str] = []

        if _PRESIDIO_AVAILABLE and self._analyzer:
            results: list[RecognizerResult] = self._analyzer.analyze(
                text=text,
                language="en",
                entities=[
                    "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER",
                    "US_SSN", "US_DRIVER_LICENSE", "DATE_TIME",
                    "CREDIT_CARD", "IP_ADDRESS", "US_ITIN",
                ],
                score_threshold=float(
                    __import__("os").getenv("PHI_DETECTION_CONFIDENCE_THRESHOLD", "0.85")
                ),
            )
            found_entities = list({r.entity_type for r in results})
            operators = {
                entity: OperatorConfig("replace", {"new_value": f"[{entity}-REDACTED]"})
                for entity in found_entities
            }
            anonymized = self._anonymizer.anonymize(
                text=text,
                analyzer_results=results,
                operators=operators,
            )
            text = anonymized.text

        # Always run regex layer as a safety net
        for label, replacement, pattern in _PATTERNS:
            if pattern.search(text):
                found_entities.append(label)
                text = pattern.sub(replacement, text)

        return text, list(set(found_entities))

    def scrub_dict(self, data: dict[str, Any], depth: int = 0) -> dict[str, Any]:
        """Recursively scrub string values in a dict."""
        if depth > 8:
            return data
        result: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, str):
                scrubbed, _ = self.scrub_text(value)
                result[key] = scrubbed
            elif isinstance(value, dict):
                result[key] = self.scrub_dict(value, depth + 1)
            elif isinstance(value, list):
                result[key] = [
                    self.scrub_dict(item, depth + 1) if isinstance(item, dict)
                    else (self.scrub_text(item)[0] if isinstance(item, str) else item)
                    for item in value
                ]
            else:
                result[key] = value
        return result

    def mask_member_pii(self, member: dict[str, Any]) -> dict[str, Any]:
        """
        Returns a safe view of a member record suitable for logging / LLM context.
        Keeps non-sensitive fields; masks PII.
        """
        plan = member.get("plan") or {}
        return {
            "member_id": member.get("member_id"),
            "first_name": member.get("first_name", "")[0] + "***" if member.get("first_name") else "***",
            "last_name": member.get("last_name", "")[0] + "***" if member.get("last_name") else "***",
            "dob": "[DOB-REDACTED]",
            "gender": member.get("gender"),
            "ssn_last4": "[SSN4-REDACTED]",
            "state": member.get("address", {}).get("state"),
            "zip": member.get("address", {}).get("zip", "")[:3] + "**",
            "plan_id": plan.get("plan_id"),
            "relationship_code": member.get("relationship_code"),
            # Nested plan kept for policy_gate / supervisor (no PII in these fields)
            "plan": {
                "plan_id": plan.get("plan_id"),
                "name": plan.get("name"),
                "bin": plan.get("bin"),
                "pcn": plan.get("pcn"),
                "group": plan.get("group"),
            },
        }


_scrubber: Optional[PHIScrubber] = None


def get_scrubber() -> PHIScrubber:
    global _scrubber
    if _scrubber is None:
        _scrubber = PHIScrubber()
    return _scrubber
