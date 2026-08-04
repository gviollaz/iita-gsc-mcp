# Exports the authenticated Starlette app. This used to export mcp.sse_app(),
# which was both the wrong transport (the server speaks streamable HTTP) and
# unauthenticated.
from server import app

__all__ = ["app"]
