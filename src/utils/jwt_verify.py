"""Optional JWT validation (JWKS) for enterprise API access."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Optional

import jwt
from jwt import PyJWKClient, PyJWKClientError

logger = logging.getLogger(__name__)


class JWTConfigurationError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    # PyJWT 2.8+; optional kwargs vary by minor version — keep defaults for portability.
    return PyJWKClient(jwks_url)


def jwt_auth_enabled() -> bool:
    if os.getenv("TRUST_API_GATEWAY_AUTH", "").lower() in ("1", "true", "yes"):
        return False
    return os.getenv("REQUIRE_AUTH", "").lower() in ("1", "true", "yes")


def verify_bearer_token(token: str) -> dict[str, Any]:
    """
    Validate RS256 (or alg from JWKS) JWT. Returns claims dict.

    Env:
      JWT_JWKS_URL — required when REQUIRE_AUTH=true
      JWT_AUDIENCE — optional (validated if set)
      JWT_ISSUER — optional (validated if set)
    """
    jwks_url = os.getenv("JWT_JWKS_URL", "").strip()
    if not jwks_url:
        raise JWTConfigurationError("REQUIRE_AUTH is set but JWT_JWKS_URL is empty")

    audience = os.getenv("JWT_AUDIENCE", "").strip() or None
    issuer = os.getenv("JWT_ISSUER", "").strip() or None

    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        decode_kw: dict[str, Any] = {
            "algorithms": ["RS256", "ES256"],
            "options": {"require": ["exp", "sub"]},
        }
        if audience:
            decode_kw["audience"] = audience
        if issuer:
            decode_kw["issuer"] = issuer
        return jwt.decode(token, signing_key.key, **decode_kw)
    except jwt.exceptions.PyJWTError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise
    except PyJWKClientError as exc:
        logger.error("JWKS client error: %s", exc)
        raise JWTConfigurationError(f"JWKS unavailable: {exc}") from exc


def describe_auth_mode() -> str:
    if os.getenv("TRUST_API_GATEWAY_AUTH", "").lower() in ("1", "true", "yes"):
        return "trust_api_gateway"
    if jwt_auth_enabled():
        return "jwt_jwks"
    return "none"
