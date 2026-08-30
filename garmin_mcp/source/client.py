"""The only module that imports `garminconnect` (docs/SCOPE.md §11).

Two entry points this pass: `bootstrap_login` (called by the CLI, needs a password)
and `auth_status` (called by the MCP tool layer, never sees a password).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from . import ratelimit
from .errors import (
    AuthExpired,
    GarminSourceError,
    InvalidCredentials,
    LoginRejected,
    NotBootstrapped,
    RateLimited,
    SourceUnavailable,
)
from .lock import garmin_lock

logger = logging.getLogger(__name__)

DEFAULT_TOKENSTORE = "/data/garmin_tokens.json"

_client: Garmin | None = None
_client_lock = threading.Lock()


def tokenstore_path() -> str:
    """Where the token file lives. Set by GARMINTOKENS in the image."""
    return os.environ.get("GARMINTOKENS") or DEFAULT_TOKENSTORE


@contextmanager
def _garmin_access(path: str) -> Iterator[None]:
    """The single doorway to Garmin. Every round trip goes through here.

    Order is fixed and matters: the cross-process lock first, then the request
    budget. Taking the budget first would let a process burn a token and then
    queue on the lock, so tokens would be spent by waiting rather than by
    requesting.

    The wait for a budget therefore happens while holding the lock. That is
    deliberate -- the alternative is both containers waking together and racing,
    and the point of the limiter is a global rate, not a per-process one.
    """
    with garmin_lock(path):
        ratelimit.acquire(path)
        yield


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


# A data-path 429 arrives as GarminConnectConnectionError carrying this text,
# not as a typed rate-limit error. _translate explains why.
_API_ERROR_429 = re.compile(r"\bAPI Error 429\b")

# What the library says when Garmin actually rejected the credentials, from
# resp_type == "INVALID_USERNAME_PASSWORD". Anything else wearing the same
# exception type is not a password problem.
_INVALID_CREDENTIALS = re.compile(
    r"Invalid Username or Password|INVALID_USERNAME_PASSWORD", re.IGNORECASE
)


def _translate(
    exc: Exception, path: str, *, during_login: bool = False
) -> GarminSourceError:
    """Map a garminconnect exception onto our vocabulary.

    `during_login` is not a convenience flag. `GarminConnectAuthenticationError`
    is raised on both of our call paths and means opposite things on each: on a
    token load it is "these tokens are no longer good"; on a credential login it
    is "this password is wrong" (the library raises it for
    `INVALID_USERNAME_PASSWORD` and stops the strategy chain immediately). The
    exception carries nothing that distinguishes them, so the only thing that
    can is the caller.

    Without it, the web form answered a mistyped password with "Garmin rejected
    the stored tokens at /data/garmin_tokens.json ... re-run the bootstrap" --
    on a machine with no token file, to somebody who was running the bootstrap.
    """
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return RateLimited()
    if isinstance(exc, GarminConnectAuthenticationError):
        if not during_login:
            return AuthExpired(path)
        # str(exc) is the library's own text. It was previously discarded, which
        # threw away the one line that said what had actually gone wrong.
        #
        # Only two of the ~15 places the library raises this type concern
        # credentials; the rest are "Invalid profile data found", "Not
        # authenticated", a bot challenge answered with a 401, and similar. So
        # the credential verdict is given only when Garmin actually said so, and
        # everything else reports what it did say instead of guessing.
        #
        # Matching on message text is fragile and deliberately so rather than
        # silently wrong, exactly as the 429 branch below is.
        detail = str(exc)
        if _INVALID_CREDENTIALS.search(detail):
            return InvalidCredentials(detail)
        return LoginRejected(detail)
    if isinstance(exc, GarminConnectConnectionError):
        # GarminConnectTooManyRequestsError is raised ONLY by the login paths.
        # On a data request, client._run_request maps every status >= 400 except
        # 404 onto GarminConnectConnectionError -- there is no 429 branch. So a
        # rate limit reaches us dressed as a connectivity error, and left
        # untranslated the agent reads it as transient and retries, which is
        # precisely what docs/SCOPE.md §6 forbids.
        #
        # Matching on the message is fragile, and deliberately so rather than
        # silently wrong: the status code is not carried on the exception. If a
        # future release adds a real 429 type, the isinstance branch above
        # catches it first and this becomes dead code.
        if _API_ERROR_429.search(str(exc)):
            return RateLimited("on a data request, not the login")
        return SourceUnavailable(str(exc))
    return SourceUnavailable(str(exc))


def _connect() -> Garmin:
    """Return a logged-in client, creating it at most once per process.

    `login()` is a real two-call round-trip to Garmin on every invocation,
    including the cached-token path. Building a client per tool call would rate
    limit us against our own server, so the instance is cached for the process
    lifetime.

    That cache is per-PROCESS, and a process is not the same thing as a client
    session. Measured on 2026-08-30: Claude Desktop spawns TWO containers for a
    single configured stdio server, both long-lived. So the token file has two
    independent readers and -- because garminconnect re-dumps tokens whenever it
    refreshes them -- two independent writers, with only an in-process lock
    between them. Cross-process safety is not solved here.
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
            with _garmin_access(path):
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
        # Bootstrap takes the lock too. It runs in its own container invocation
        # and can coincide with a serving container refreshing tokens; without
        # this, a bootstrap write and a refresh write race for the same file.
        with _garmin_access(path):
            api.login(tokenstore=path)
    except Exception as exc:
        raise _translate(exc, path, during_login=True) from exc

    # Tokens are the credential now. Keep them owner-only.
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)

    return api.display_name or email
