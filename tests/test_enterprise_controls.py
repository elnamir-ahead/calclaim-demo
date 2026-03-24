"""Enterprise controls: MCP URL policy, JWT mode helpers."""

import os

import pytest

from src.utils.mcp_workflow_client import MCPURLError, validate_mcp_url
from src.utils.jwt_verify import describe_auth_mode, jwt_auth_enabled


def test_validate_mcp_url_allows_default_localhost():
    validate_mcp_url("http://127.0.0.1:8765/mcp")


def test_validate_mcp_url_scheme_restricted(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_SCHEMES", "https")
    with pytest.raises(MCPURLError, match="scheme"):
        validate_mcp_url("http://127.0.0.1:8765/mcp")


def test_validate_mcp_url_host_allowlist(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp.internal.example")
    validate_mcp_url("https://mcp.internal.example/mcp")
    with pytest.raises(MCPURLError, match="not in MCP_ALLOWED_HOSTS"):
        validate_mcp_url("http://127.0.0.1:8765/mcp")


def test_jwt_auth_mode_env(monkeypatch):
    monkeypatch.delenv("REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("TRUST_API_GATEWAY_AUTH", raising=False)
    assert jwt_auth_enabled() is False
    assert describe_auth_mode() == "none"

    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("JWT_JWKS_URL", "https://example.com/.well-known/jwks.json")
    assert jwt_auth_enabled() is True

    monkeypatch.setenv("TRUST_API_GATEWAY_AUTH", "true")
    assert jwt_auth_enabled() is False
    assert describe_auth_mode() == "trust_api_gateway"
