"""The MCP tool layer.

This module must NEVER import `garminconnect` (docs/SCOPE.md §11). It talks to
`garmin_mcp.source` and nothing else.
"""

from __future__ import annotations

import logging
import os

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__
from .source import client as source
from .source.errors import GarminSourceError

logger = logging.getLogger(__name__)


def _server_version() -> str:
    """Which build is actually answering.

    `docker run` does not re-pull a moving tag, so a config pinned to `latest`
    silently keeps running whatever was pulled last. Without this, "am I on the
    current image?" is unanswerable from inside a conversation. GARMIN_MCP_BUILD
    is stamped by CI with the commit; it is absent on a local build.
    """
    build = os.environ.get("GARMIN_MCP_BUILD", "").strip()
    return f"{__version__}+{build}" if build else __version__


# Passed to the SDK so the build also appears in `serverInfo` on the initialize
# handshake, not only inside a tool result. That is the first thing a client
# shows and the only thing visible without calling anything -- it was empty, so
# a client could display the server as connected while saying nothing about
# which build had connected. Exactly the gap `_server_version` exists to close,
# left open at the one place it is cheapest to read.
mcp = MCPServer("garmin", version=_server_version())


class AuthStatus(BaseModel):
    """Wire model. Every field is named so a reader knows what it means without
    guessing -- including the unit system, because a bare number elsewhere in
    this server would be ambiguous (docs/SCOPE.md §5)."""

    authenticated: bool = Field(description="True only if Garmin accepted the stored tokens just now.")
    display_name: str = Field(description="Garmin Connect display name for the account.")
    full_name: str = Field(description="Full name on the account, may be empty.")
    unit_system: str = Field(description="Account measurement system, e.g. 'metric' or 'statute'.")
    tokens_path: str = Field(description="Path to the token file inside the container.")
    tokens_modified_utc: str = Field(description="When the token file was last written, ISO-8601 UTC.")
    server_version: str = Field(description="Version of this MCP server, plus the build commit when CI-built.")


@mcp.tool(
    title="Garmin authentication status",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
def garmin_auth_status() -> AuthStatus:
    """Check whether this server can still talk to Garmin Connect.

    Makes a live authenticated call, so a success proves the stored tokens work
    right now. Use this first when other Garmin tools start failing.
    """
    try:
        status = source.auth_status()
    except GarminSourceError as exc:
        # str(exc) carries the actionable message; the agent should surface it
        # rather than retrying.
        raise ToolError(str(exc)) from exc

    return AuthStatus(
        authenticated=status.authenticated,
        display_name=status.display_name,
        full_name=status.full_name,
        unit_system=status.unit_system,
        tokens_path=status.tokens_path,
        tokens_modified_utc=status.tokens_modified_utc,
        server_version=_server_version(),
    )
