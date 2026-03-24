"""
LangGraph bridge to CalcClaim MCP server (streamable HTTP).

Set ``CALCLAIM_MCP_URL`` (e.g. ``http://127.0.0.1:8765/mcp``) and install the ``mcp``
package. When unset or import fails, the workflow skips MCP enrichment.

Enterprise:
  ``MCP_ALLOWED_HOSTS`` — comma-separated hostname allowlist (empty = allow any host).
  ``MCP_ALLOWED_SCHEMES`` — default ``http,https``; restrict in prod (e.g. ``https`` only).
  ``MCP_BEARER_TOKEN`` — optional ``Authorization: Bearer`` for MCP HTTP (if SDK supports it).

Uses ``asyncio.run`` inside a sync node; LangGraph runs sync nodes in a worker
thread for ``ainvoke``, so a nested event loop is avoided there.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class MCPURLError(ValueError):
    """MCP URL failed enterprise allowlist checks."""


def validate_mcp_url(url: str) -> None:
    """
    Enforce optional host/scheme allowlists to reduce SSRF and misconfiguration.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise MCPURLError(f"MCP URL scheme not allowed: {parsed.scheme!r}")

    schemes = {
        s.strip().lower()
        for s in os.getenv("MCP_ALLOWED_SCHEMES", "http,https").split(",")
        if s.strip()
    }
    if parsed.scheme.lower() not in schemes:
        raise MCPURLError(
            f"MCP URL scheme {parsed.scheme!r} not in MCP_ALLOWED_SCHEMES={schemes!r}"
        )

    hosts_env = os.getenv("MCP_ALLOWED_HOSTS", "").strip()
    if not hosts_env:
        return
    host = (parsed.hostname or "").lower()
    allowed = {h.strip().lower() for h in hosts_env.split(",") if h.strip()}
    if host not in allowed:
        raise MCPURLError(
            f"MCP host {host!r} not in MCP_ALLOWED_HOSTS — refusing connection"
        )


def _mcp_optional_headers() -> Optional[dict[str, str]]:
    token = os.getenv("MCP_BEARER_TOKEN", "").strip()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def _parse_tool_result(result: Any) -> Any:
    """Turn MCP CallToolResult.content into a Python object when JSON text."""
    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}
    return None


async def _fetch_formulary_async(mcp_url: str, ndc: str, plan_code: str) -> Optional[dict[str, Any]]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    validate_mcp_url(mcp_url)
    headers = _mcp_optional_headers()
    try:
        if headers:
            client_cm = streamable_http_client(mcp_url, headers=headers)
        else:
            client_cm = streamable_http_client(mcp_url)
    except TypeError:
        if headers:
            logger.warning(
                "MCP_BEARER_TOKEN set but streamable_http_client has no headers= — "
                "upgrade mcp package or terminate TLS at ALB with auth"
            )
        client_cm = streamable_http_client(mcp_url)

    async with client_cm as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            res = await session.call_tool(
                "formulary_tier_lookup",
                arguments={"ndc": ndc, "plan_code": plan_code},
            )
            parsed = _parse_tool_result(res)
            if isinstance(parsed, dict):
                return parsed
            return {"ok": False, "error": "unexpected_tool_payload", "raw": str(parsed)}


def run_mcp_formulary_sync(mcp_url: str, ndc: str, plan_code: str) -> Optional[dict[str, Any]]:
    """Blocking wrapper for LangGraph sync nodes."""
    if not mcp_url or not ndc:
        return None
    try:
        return asyncio.run(_fetch_formulary_async(mcp_url, ndc, plan_code))
    except RuntimeError as exc:
        # Nested loop (rare if node runs on main thread with active loop)
        if "asyncio.run() cannot be called from a running event loop" in str(exc):
            logger.warning("MCP bridge: falling back to new-task loop for formulary tool")
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_fetch_formulary_async(mcp_url, ndc, plan_code))
            finally:
                loop.close()
        raise
    except Exception as exc:
        logger.warning("MCP formulary tool failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def mcp_package_available() -> bool:
    try:
        import mcp  # noqa: F401

        return True
    except ImportError:
        return False
