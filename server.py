"""IITA GSC MCP server entry point — v0.2.0.

Adds authentication to the Search Console MCP server. Until v0.2.0 this
server answered every request unauthenticated: anyone who knew the Railway
URL could call `tools/list` and read the Search Console data of iita.com.ar.

Dual-mode auth, same pattern as IITA-googleads-mcp:

  MCP_OAUTH_ENABLED=true (default — what googleads runs in production):
    - Full OAuth 2.1 flow (RFC 9728 + RFC 8414 + RFC 7591 + PKCE)
    - Connector URL: https://<host>/mcp

  MCP_OAUTH_ENABLED=false (fallback for clients that can't do OAuth):
    - OAuth discovery endpoints return 404
    - /mcp/{token} → URL-token auth
    - /mcp → Bearer token auth

MCP_AUTH_TOKEN is required in both modes. The server refuses to start
without it: booting unauthenticated is the bug this module exists to fix.
"""

from __future__ import annotations

import contextlib
import hmac
import logging
import os
import sys
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

_VERSION = "0.2.0"
SERVICE_NAME = "iita_gsc_mcp"
DOCS_URL = "https://github.com/gviollaz/iita-gsc-mcp"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class ConfigError(RuntimeError):
    """Raised when required config is missing or invalid."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _truthy(value: str) -> bool:
    return value.lower() in ("true", "1", "yes")


def _require_auth_token() -> str:
    token = _env("MCP_AUTH_TOKEN")
    if not token:
        raise ConfigError(
            "Missing required env var: MCP_AUTH_TOKEN. Generate one with "
            "`openssl rand -hex 32` and set it in the Railway service. "
            "Refusing to start: without it the server would expose Search "
            "Console data to anyone who knows the URL."
        )
    return token


OAUTH_ENABLED = _truthy(_env("MCP_OAUTH_ENABLED", "true"))
PORT = int(_env("PORT", "8080") or "8080")
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

PUBLIC_PATHS = frozenset(
    {
        "/",
        "/health",
        "/favicon.ico",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
        "/oauth/register",
        "/oauth/authorize",
        "/oauth/token",
    }
)


def _timing_safe_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class OAuthBearerMiddleware(BaseHTTPMiddleware):
    """Validate OAuth 2.1 Bearer tokens on protected paths."""

    def __init__(self, app, mcp_auth_token: str) -> None:
        super().__init__(app)
        self._mcp_auth_token = mcp_auth_token

    def _unauthorized(self, request: Request, reason: str) -> JSONResponse:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        issuer = f"{scheme}://{host}"
        www_auth = (
            f'Bearer realm="mcp", '
            f'resource_metadata="{issuer}/.well-known/oauth-protected-resource"'
        )
        return JSONResponse(
            {"error": "invalid_token", "error_description": reason},
            status_code=401,
            headers={"WWW-Authenticate": www_auth},
        )

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in PUBLIC_PATHS:
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return self._unauthorized(request, "missing Bearer token")

        token = auth[7:].strip()

        from oauth import OAuthConfig, validate_access_token

        cfg = OAuthConfig.from_request(request, self._mcp_auth_token)
        claims = validate_access_token(cfg, token)
        if not claims:
            return self._unauthorized(request, "invalid or expired token")

        request.state.oauth_claims = claims
        logger.debug("Auth OK client_id=%s path=%s", claims.get("client_id"), path)
        return await call_next(request)


class UrlTokenRewriteMiddleware(BaseHTTPMiddleware):
    """Rewrite /mcp/{token} to /mcp after validating the URL token."""

    def __init__(self, app, mcp_auth_token: str) -> None:
        super().__init__(app)
        self._token = mcp_auth_token

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/mcp/") and not path.startswith("/mcp/sse"):
            parts = path.split("/", 3)
            if len(parts) >= 3:
                token = parts[2]
                if not _timing_safe_eq(token, self._token):
                    return JSONResponse(
                        {"error": "invalid_token", "error_description": "Invalid URL token"},
                        status_code=401,
                    )
                new_path = "/mcp" + ("/" + parts[3] if len(parts) > 3 else "")
                request.scope["path"] = new_path
                request.state.auth_via = "url_token"
        return await call_next(request)


class SimpleBearerMiddleware(BaseHTTPMiddleware):
    """Simple Bearer token check for /mcp when not using OAuth."""

    def __init__(self, app, mcp_auth_token: str) -> None:
        super().__init__(app)
        self._token = mcp_auth_token

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if getattr(request.state, "auth_via", None) == "url_token":
            return await call_next(request)
        if path in PUBLIC_PATHS:
            return await call_next(request)
        if path.startswith("/mcp"):
            auth = request.headers.get("authorization", "")
            if not auth.lower().startswith("bearer "):
                return JSONResponse(
                    {"error": "invalid_token", "error_description": "missing Bearer token"},
                    status_code=401,
                    headers={"WWW-Authenticate": f'Bearer realm="{SERVICE_NAME}"'},
                )
            if not _timing_safe_eq(auth[7:].strip(), self._token):
                return JSONResponse(
                    {"error": "invalid_token", "error_description": "invalid Bearer token"},
                    status_code=401,
                    headers={"WWW-Authenticate": f'Bearer realm="{SERVICE_NAME}"'},
                )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


def _issuer(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return f"{scheme}://{host}"


async def root(request: Request) -> Response:
    issuer = _issuer(request)
    info = {
        "service": SERVICE_NAME,
        "version": _VERSION,
        "status": "ok",
        "auth_mode": "oauth" if OAUTH_ENABLED else "url_token",
        "docs": DOCS_URL,
        "mcp_endpoint": f"{issuer}/mcp",
    }
    if OAUTH_ENABLED:
        info["oauth"] = {
            "protected_resource_metadata": f"{issuer}/.well-known/oauth-protected-resource",
            "authorization_server_metadata": f"{issuer}/.well-known/oauth-authorization-server",
        }
    return JSONResponse(info)


async def health(_request: Request) -> Response:
    return JSONResponse({"status": "healthy", "version": _VERSION})


async def mcp_head(_request: Request) -> Response:
    return Response(
        status_code=200,
        headers={"MCP-Protocol-Version": "2025-03-26", "Allow": "POST, HEAD"},
    )


async def oauth_discovery_404(request: Request) -> Response:
    logger.info("[OAuth] Discovery hit: %s -> 404 (OAuth disabled)", request.url.path)
    return JSONResponse({"error": "OAuth not configured on this server"}, status_code=404)


async def oauth_register_404(_request: Request) -> Response:
    logger.info("[OAuth] DCR hit -> 404 (OAuth disabled)")
    return JSONResponse({"error": "Dynamic client registration not supported"}, status_code=404)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app() -> Starlette:
    mcp_auth_token = _require_auth_token()

    # Imported here so a missing MCP_AUTH_TOKEN fails before the Google
    # client and tool registration are touched.
    import main

    mcp = main.mcp
    mcp_app = mcp.streamable_http_app()

    tool_count = len(mcp._tool_manager._tools) if hasattr(mcp, "_tool_manager") else "unknown"
    logger.info(
        "Starting %s v%s port=%d auth_mode=%s tools=%s",
        SERVICE_NAME,
        _VERSION,
        PORT,
        "oauth" if OAUTH_ENABLED else "url_token",
        tool_count,
    )

    @contextlib.asynccontextmanager
    async def combined_lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            logger.info("MCP session manager started")
            try:
                yield
            finally:
                logger.info("MCP session manager shutting down")

    if OAUTH_ENABLED:
        from oauth import build_handlers

        handlers = build_handlers(mcp_auth_token)
        routes = [
            Route("/", endpoint=root, methods=["GET"]),
            Route("/health", endpoint=health, methods=["GET"]),
            Route(
                "/.well-known/oauth-protected-resource",
                endpoint=handlers["protected_resource_metadata"],
                methods=["GET"],
            ),
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=handlers["authorization_server_metadata"],
                methods=["GET"],
            ),
            Route("/oauth/register", endpoint=handlers["register"], methods=["POST"]),
            Route("/oauth/authorize", endpoint=handlers["authorize"], methods=["GET"]),
            Route("/oauth/token", endpoint=handlers["token"], methods=["POST"]),
            Mount("/", app=mcp_app),
        ]
        middleware = [Middleware(OAuthBearerMiddleware, mcp_auth_token=mcp_auth_token)]
        logger.info("[AUTH] OAuth 2.1 mode ACTIVE")
    else:
        routes = [
            Route("/", endpoint=root, methods=["GET"]),
            Route("/health", endpoint=health, methods=["GET"]),
            Route(
                "/.well-known/oauth-protected-resource",
                endpoint=oauth_discovery_404,
                methods=["GET"],
            ),
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=oauth_discovery_404,
                methods=["GET"],
            ),
            Route("/oauth/register", endpoint=oauth_register_404, methods=["POST"]),
            Route("/mcp/{token:path}", endpoint=mcp_head, methods=["HEAD"]),
            Route("/mcp", endpoint=mcp_head, methods=["HEAD"]),
            Mount("/", app=mcp_app),
        ]
        middleware = [
            Middleware(UrlTokenRewriteMiddleware, mcp_auth_token=mcp_auth_token),
            Middleware(SimpleBearerMiddleware, mcp_auth_token=mcp_auth_token),
        ]
        logger.info("[AUTH] URL-token mode ACTIVE")

    return Starlette(routes=routes, middleware=middleware, lifespan=combined_lifespan)


def _setup_logging() -> None:
    # An unrecognized level makes basicConfig raise ValueError at import time,
    # which crash-loops the deploy over a logging setting. Fall back instead.
    level = LOG_LEVEL if LOG_LEVEL in logging.getLevelNamesMapping() else "INFO"
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        stream=sys.stderr,
    )


_setup_logging()
app = build_app()


def _uvicorn_log_level() -> str:
    # uvicorn accepts a fixed set of names and raises KeyError on anything else,
    # killing the process at startup. Python's logging accepts "WARN" as an
    # alias for "WARNING"; uvicorn does not. Normalize, and fall back to "info"
    # for any unrecognized value so a bad LOG_LEVEL can't crash-loop the deploy.
    allowed = {"critical", "error", "warning", "info", "debug", "trace"}
    lvl = LOG_LEVEL.lower()
    if lvl == "warn":
        lvl = "warning"
    return lvl if lvl in allowed else "info"


def main_entry() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level=_uvicorn_log_level())


if __name__ == "__main__":
    main_entry()
