"""
LaunchDarkly — runtime feature flags for CalcClaim (optional).

If LAUNCHDARKLY_SDK_KEY is unset, ``evaluate_calclaim_flags`` returns env-only defaults
(USE_AGENTCORE, USE_MCP_TOOLS) with no network calls.

Flag keys default to ``calclaim-use-agentcore`` and ``calclaim-use-mcp-tools``; override
via LAUNCHDARKLY_FLAG_* env vars to match your LaunchDarkly project.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_ld_initialized = False

DEFAULT_FLAG_AGENTCORE = "calclaim-use-agentcore"
DEFAULT_FLAG_MCP = "calclaim-use-mcp-tools"


def _env_bool(name: str, default_true: bool = True) -> bool:
    raw = os.getenv(name, "true" if default_true else "false").lower()
    return raw not in ("false", "0", "no", "off")


def _sdk_key_set() -> bool:
    key = (os.getenv("LAUNCHDARKLY_SDK_KEY") or "").strip()
    return bool(key) and key.lower() not in ("changeme", "placeholder")


def init_launchdarkly() -> None:
    """Initialize the LD client once (Lambda cold start). Safe to call repeatedly."""
    global _ld_initialized
    if _ld_initialized:
        return
    if not _sdk_key_set():
        logger.info("LaunchDarkly: LAUNCHDARKLY_SDK_KEY not set — using env defaults only")
        _ld_initialized = True
        return
    try:
        import ldclient
        from ldclient.config import Config
    except ImportError:
        logger.warning("LaunchDarkly SDK not installed — using env defaults only")
        _ld_initialized = True
        return

    sdk_key = os.getenv("LAUNCHDARKLY_SDK_KEY", "").strip()
    timeout = float(os.getenv("LAUNCHDARKLY_INIT_TIMEOUT_SEC", "5"))
    ldclient.set_config(Config(sdk_key=sdk_key))
    client = ldclient.get()
    if not client.is_initialized():
        ok = client.wait_for_initialization(timeout=timeout)
        if not ok:
            logger.warning("LaunchDarkly: initialization timed out — flags may use defaults")
    _ld_initialized = True
    logger.info("LaunchDarkly client ready")


def shutdown_launchdarkly() -> None:
    """Close LD client on process shutdown (local uvicorn; Lambda freeze is fine)."""
    global _ld_initialized
    if not _sdk_key_set():
        return
    try:
        import ldclient

        c = ldclient.get()
        if c and c.is_initialized():
            c.close()
    except Exception as exc:
        logger.debug("LaunchDarkly shutdown: %s", exc)
    _ld_initialized = False


def evaluate_calclaim_flags(actor_id: str) -> dict[str, Any]:
    """
    Return effective booleans for AgentCore and MCP tools.

    Keys: ``use_agentcore``, ``use_mcp_tools`` (always present).
    """
    defaults = {
        "use_agentcore": _env_bool("USE_AGENTCORE", True),
        "use_mcp_tools": _env_bool("USE_MCP_TOOLS", True),
    }
    if not _sdk_key_set():
        return defaults

    try:
        import ldclient
        from ldclient import Context
    except ImportError:
        return defaults

    init_launchdarkly()
    client = ldclient.get()
    if not client.is_initialized():
        return defaults

    flag_ac = os.getenv("LAUNCHDARKLY_FLAG_USE_AGENTCORE", DEFAULT_FLAG_AGENTCORE).strip()
    flag_mcp = os.getenv("LAUNCHDARKLY_FLAG_USE_MCP_TOOLS", DEFAULT_FLAG_MCP).strip()
    key = (actor_id or "anonymous").strip() or "anonymous"
    ctx = Context.builder(key).kind("user").build()

    try:
        use_ac = bool(client.variation(flag_ac, ctx, defaults["use_agentcore"]))
        use_mcp = bool(client.variation(flag_mcp, ctx, defaults["use_mcp_tools"]))
    except Exception as exc:
        logger.warning("LaunchDarkly variation error: %s — using env defaults", exc)
        return defaults

    return {"use_agentcore": use_ac, "use_mcp_tools": use_mcp}


def use_agentcore_effective(state: dict[str, Any]) -> bool:
    """Prefer ``state['feature_flags']['use_agentcore']`` when present; else ``USE_AGENTCORE`` env."""
    ff = state.get("feature_flags")
    if isinstance(ff, dict) and "use_agentcore" in ff:
        return bool(ff["use_agentcore"])
    return _env_bool("USE_AGENTCORE", True)


def use_mcp_tools_effective(state: dict[str, Any]) -> bool:
    """Prefer ``state['feature_flags']['use_mcp_tools']`` when present; else ``USE_MCP_TOOLS`` env."""
    ff = state.get("feature_flags")
    if isinstance(ff, dict) and "use_mcp_tools" in ff:
        return bool(ff["use_mcp_tools"])
    return _env_bool("USE_MCP_TOOLS", True)
