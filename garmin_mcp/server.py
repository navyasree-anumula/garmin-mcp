"""The MCP tool layer.

This module must NEVER import `garminconnect` (docs/SCOPE.md §11). It talks to
`garmin_mcp.source` and nothing else.
"""

from __future__ import annotations

import logging

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .source import client as source
from .source.errors import GarminSourceError

logger = logging.getLogger(__name__)

mcp = MCPServer("garmin")


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
    )
