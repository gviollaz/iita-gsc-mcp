"""OAuth 2.1 Authorization Server for the MCP protocol.

Port of the implementation in IITA-googleads-mcp (src/googleads_mcp/oauth.py),
kept deliberately close to it so both can be diffed side by side. Only the
service name embedded in the signing-secret derivation differs, so a token
minted by one server is not valid on the other.

Implements the minimal subset of OAuth 2.1 + RFC 8414 + RFC 9728 + RFC 7591
required by the MCP authorization spec.

Design choices (same as googleads):
- Single-user: the only end-user is the deployment owner.
- Auto-consent: /authorize redirects back with a code, no consent screen.
  Safe because redirect_uri is bound into the code and PKCE is mandatory.
- Stateless: authorization codes AND access tokens are self-contained JWTs.
  Codes have a 60 s TTL, access tokens 24 h.
- Symmetric signing: HS256 with a secret derived from MCP_AUTH_TOKEN.
- No refresh tokens: clients re-auth after 24 h.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import jwt
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

logger = logging.getLogger(__name__)

# --- Constants ---------------------------------------------------------------

SERVICE_NAME = "iita_gsc_mcp"
AUTHORIZATION_CODE_TTL_SECONDS = 60
ACCESS_TOKEN_TTL_SECONDS = 24 * 3600  # 24 h
SUPPORTED_SCOPES = ["mcp"]
SUPPORTED_RESPONSE_TYPES = ["code"]
SUPPORTED_GRANT_TYPES = ["authorization_code"]
SUPPORTED_CODE_CHALLENGE_METHODS = ["S256"]
SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS = ["none"]  # PKCE, public clients


# --- Helpers -----------------------------------------------------------------


def _derive_signing_secret(mcp_auth_token: str) -> bytes:
    """Derive a stable signing key from MCP_AUTH_TOKEN.

    The service name is part of the HMAC message so the same MCP_AUTH_TOKEN
    reused across servers still yields different keys.
    """
    return hmac.new(
        key=mcp_auth_token.encode("utf-8"),
        msg=f"{SERVICE_NAME}:oauth:signing:v1".encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()


def _now() -> int:
    return int(time.time())


def _pkce_verify(code_verifier: str, code_challenge: str, method: str) -> bool:
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return hmac.compare_digest(expected, code_challenge)


# --- Public API --------------------------------------------------------------


@dataclass(frozen=True)
class OAuthConfig:
    """Runtime config for the OAuth handlers."""

    issuer: str
    signing_secret: bytes

    @classmethod
    def from_request(cls, request: Request, mcp_auth_token: str) -> "OAuthConfig":
        # Prefer X-Forwarded-* headers set by Railway's edge proxy
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        issuer = f"{scheme}://{host}"
        return cls(issuer=issuer, signing_secret=_derive_signing_secret(mcp_auth_token))


def issue_access_token(cfg: OAuthConfig, client_id: str, subject: str = "owner") -> str:
    """Mint a self-contained JWT access token."""
    now = _now()
    payload = {
        "iss": cfg.issuer,
        "sub": subject,
        "aud": cfg.issuer,
        "client_id": client_id,
        "scope": "mcp",
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_SECONDS,
        "token_type": "access",
    }
    return jwt.encode(payload, cfg.signing_secret, algorithm="HS256")


def validate_access_token(cfg: OAuthConfig, token: str) -> dict[str, Any] | None:
    """Return decoded claims if the token is valid, else None."""
    try:
        claims = jwt.decode(
            token,
            cfg.signing_secret,
            algorithms=["HS256"],
            audience=cfg.issuer,
            issuer=cfg.issuer,
        )
        if claims.get("token_type") != "access":
            return None
        return claims
    except jwt.InvalidTokenError as e:
        logger.debug("Access token validation failed: %s", e)
        return None


def _issue_authorization_code(
    cfg: OAuthConfig,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str,
) -> str:
    now = _now()
    payload = {
        "iss": cfg.issuer,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
        "iat": now,
        "exp": now + AUTHORIZATION_CODE_TTL_SECONDS,
        "token_type": "code",
    }
    return jwt.encode(payload, cfg.signing_secret, algorithm="HS256")


def _decode_authorization_code(cfg: OAuthConfig, code: str) -> dict[str, Any] | None:
    try:
        claims = jwt.decode(
            code,
            cfg.signing_secret,
            algorithms=["HS256"],
            issuer=cfg.issuer,
            options={"verify_aud": False},
        )
        if claims.get("token_type") != "code":
            return None
        return claims
    except jwt.InvalidTokenError as e:
        logger.debug("Authorization code validation failed: %s", e)
        return None


# --- HTTP handlers -----------------------------------------------------------


def build_handlers(mcp_auth_token: str):
    """Build Starlette route handlers. Returns a dict of named handlers."""

    async def protected_resource_metadata(request: Request) -> Response:
        """RFC 9728: tell the client where the authorization server is."""
        cfg = OAuthConfig.from_request(request, mcp_auth_token)
        return JSONResponse(
            {
                "resource": cfg.issuer,
                "authorization_servers": [cfg.issuer],
                "scopes_supported": SUPPORTED_SCOPES,
                "bearer_methods_supported": ["header"],
                "resource_documentation": f"{cfg.issuer}/",
            }
        )

    async def authorization_server_metadata(request: Request) -> Response:
        """RFC 8414: advertise the AS endpoints and capabilities."""
        cfg = OAuthConfig.from_request(request, mcp_auth_token)
        return JSONResponse(
            {
                "issuer": cfg.issuer,
                "authorization_endpoint": f"{cfg.issuer}/oauth/authorize",
                "token_endpoint": f"{cfg.issuer}/oauth/token",
                "registration_endpoint": f"{cfg.issuer}/oauth/register",
                "response_types_supported": SUPPORTED_RESPONSE_TYPES,
                "grant_types_supported": SUPPORTED_GRANT_TYPES,
                "code_challenge_methods_supported": SUPPORTED_CODE_CHALLENGE_METHODS,
                "token_endpoint_auth_methods_supported": SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS,
                "scopes_supported": SUPPORTED_SCOPES,
                "service_documentation": f"{cfg.issuer}/",
            }
        )

    async def register(request: Request) -> Response:
        """RFC 7591: Dynamic Client Registration.

        Nothing is persisted — a random client_id is echoed back and the
        client's redirect_uri is validated at authorization time instead.
        """
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        redirect_uris = body.get("redirect_uris") or []
        if not isinstance(redirect_uris, list) or not redirect_uris:
            return JSONResponse(
                {"error": "invalid_redirect_uri", "error_description": "redirect_uris is required"},
                status_code=400,
            )

        client_id = f"mcp-client-{secrets.token_urlsafe(16)}"
        response = {
            "client_id": client_id,
            "client_id_issued_at": _now(),
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": body.get("grant_types") or ["authorization_code"],
            "response_types": body.get("response_types") or ["code"],
            "scope": body.get("scope") or "mcp",
            "client_name": body.get("client_name") or "MCP Client",
        }
        logger.info("Registered new OAuth client %s", client_id)
        return JSONResponse(response, status_code=201)

    async def authorize(request: Request) -> Response:
        """Authorization endpoint: auto-consents and redirects with a code."""
        params = request.query_params

        response_type = params.get("response_type")
        client_id = params.get("client_id")
        redirect_uri = params.get("redirect_uri")
        state = params.get("state", "")
        code_challenge = params.get("code_challenge")
        code_challenge_method = params.get("code_challenge_method", "S256")
        scope = params.get("scope", "mcp")

        if response_type != "code":
            return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
        if not client_id or not redirect_uri:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "client_id and redirect_uri are required"},
                status_code=400,
            )
        if not code_challenge:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "PKCE code_challenge is required"},
                status_code=400,
            )
        if code_challenge_method not in SUPPORTED_CODE_CHALLENGE_METHODS:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "code_challenge_method must be S256"},
                status_code=400,
            )

        cfg = OAuthConfig.from_request(request, mcp_auth_token)
        code = _issue_authorization_code(
            cfg,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope=scope,
        )

        query = {"code": code}
        if state:
            query["state"] = state
        separator = "&" if "?" in redirect_uri else "?"
        redirect_to = f"{redirect_uri}{separator}{urlencode(query)}"

        logger.info("Authorized client_id=%s, redirecting to %s", client_id, redirect_uri)
        return RedirectResponse(redirect_to, status_code=302)

    async def token(request: Request) -> Response:
        """Token endpoint: exchange authorization code for access token (PKCE)."""
        try:
            form = await request.form()
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        grant_type = form.get("grant_type")
        if grant_type != "authorization_code":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        code = form.get("code")
        redirect_uri = form.get("redirect_uri")
        client_id = form.get("client_id")
        code_verifier = form.get("code_verifier")

        if not code or not redirect_uri or not client_id or not code_verifier:
            return JSONResponse(
                {
                    "error": "invalid_request",
                    "error_description": "code, redirect_uri, client_id, and code_verifier are required",
                },
                status_code=400,
            )

        cfg = OAuthConfig.from_request(request, mcp_auth_token)
        code_claims = _decode_authorization_code(cfg, code)
        if not code_claims:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "code invalid or expired"},
                status_code=400,
            )

        if code_claims.get("client_id") != client_id:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "client_id mismatch"}, status_code=400
            )
        if code_claims.get("redirect_uri") != redirect_uri:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "redirect_uri mismatch"}, status_code=400
            )

        if not _pkce_verify(
            code_verifier,
            code_claims["code_challenge"],
            code_claims["code_challenge_method"],
        ):
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "PKCE verification failed"},
                status_code=400,
            )

        access_token = issue_access_token(cfg, client_id=client_id)
        logger.info("Issued access token for client_id=%s", client_id)
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": ACCESS_TOKEN_TTL_SECONDS,
                "scope": code_claims.get("scope", "mcp"),
            }
        )

    return {
        "protected_resource_metadata": protected_resource_metadata,
        "authorization_server_metadata": authorization_server_metadata,
        "register": register,
        "authorize": authorize,
        "token": token,
    }
