"""The only module that imports `garminconnect` (docs/SCOPE.md §11).

Two entry points this pass: `bootstrap_login` (called by the CLI, needs a password)
and `auth_status` (called by the MCP tool layer, never sees a password).
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .errors import (
    AuthExpired,
    GarminSourceError,
    NotBootstrapped,
    RateLimited,
    SourceUnavailable,
)

logger = logging.getLogger(__name__)

DEFAULT_TOKENSTORE = "/data/garmin_tokens.json"

_client: Garmin | None = None
_client_lock = threading.Lock()


def tokenstore_path() -> str:
    """Where the token file lives. Set by GARMINTOKENS in the image."""
    return os.environ.get("GARMINTOKENS") or DEFAULT_TOKENSTORE


@dataclass(frozen=True)
class AuthStatus:
    """Plain result type. Deliberately not a pydantic model: `source/` does not
    know that MCP exists. The server layer maps this onto the wire model."""

    authenticated: bool
    display_name: str
    full_name: str
    unit_system: str
    tokens_path: str
    tokens_modified_utc: str


def _translate(exc: Exception, path: str) -> GarminSourceError:
    """Map a garminconnect exception onto our vocabulary."""
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return RateLimited()
    if isinstance(exc, GarminConnectAuthenticationError):
        return AuthExpired(path)
    if isinstance(exc, GarminConnectConnectionError):
        return SourceUnavailable(str(exc))
    return SourceUnavailable(str(exc))


def _connect() -> Garmin:
    """Return a logged-in client, creating it at most once per process.

    `login()` is a real two-call round-trip to Garmin on every invocation,
    including the cached-token path. Building a client per tool call would rate
    limit us against our own server, so the instance is cached for the process
    lifetime -- which under stdio is exactly one client session.
    """
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:
            return _client

        path = tokenstore_path()

        # Checked explicitly, and this matters: garminconnect swallows a missing
        # token file in a broad `except Exception`, sets tokens_loaded = False,
        # and then raises GarminConnectAuthenticationError("Username and password
        # are required") -- the SAME exception a genuinely rejected token raises.
        # Without this check the two are indistinguishable and the operator is
        # told to re-authenticate when they have simply never bootstrapped.
        if not Path(path).is_file():
            raise NotBootstrapped(path)

        # No email, no password. That absence is load-bearing: garminconnect's
        # fallback from rejected tokens to a full credential login is guarded on
        # `username and password` being present, so with neither set it re-raises
        # instead of silently logging in. This is what makes the serving process
        # token-only (docs/SCOPE.md §4).
        api = Garmin()
        try:
            api.login(tokenstore=path)
        except FileNotFoundError as exc:
            raise NotBootstrapped(path) from exc
        except Exception as exc:
            raise _translate(exc, path) from exc

        _client = api
        return api


def auth_status() -> AuthStatus:
    """Report whether the stored tokens still work.

    Non-vacuous by construction: the proof is that `_connect()` completed, since
    `login()` ends in a real authenticated profile fetch that raises when the
    token is rejected. `get_full_name()` on its own is only a cached attribute
    read and proves nothing.
    """
    api = _connect()
    path = tokenstore_path()

    try:
        modified = datetime.fromtimestamp(Path(path).stat().st_mtime, tz=UTC)
        modified_iso = modified.isoformat(timespec="seconds")
    except OSError:
        modified_iso = "unknown"

    return AuthStatus(
        authenticated=True,
        display_name=api.display_name or "",
        full_name=api.get_full_name() or "",
        unit_system=api.get_unit_system() or "unknown",
        tokens_path=path,
        tokens_modified_utc=modified_iso,
    )


def bootstrap_login(
    email: str,
    password: str,
    prompt_mfa: Callable[[], str],
    tokenstore: str | None = None,
) -> str:
    """Exchange a password for tokens, once, from the CLI.

    The password is used here and never persisted -- garminconnect itself sets
    `self.password = None` after a successful login. Only the token file is
    written. This function must never be reachable from an MCP tool.
    """
    path = tokenstore or tokenstore_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    api = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    try:
        api.login(tokenstore=path)
    except Exception as exc:
        raise _translate(exc, path) from exc

    # Tokens are the credential now. Keep them owner-only.
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)

    return api.display_name or email
