"""The on-demand credential UI: one status page and one login form.

Loopback is not the same as safe (docs/SCOPE.md §7a). The threat here is not
somebody on the network -- it is the browser already running on this machine.
Any page you visit can issue requests to `127.0.0.1`, and DNS rebinding defeats
a naive origin check by making a hostname the browser already trusts resolve to
loopback. So every request passes one doorway, `LoopbackGuard`, before it
reaches a route.

**Where the loopback guarantee actually comes from.** §7a says "bind 127.0.0.1
only, never 0.0.0.0", and that is right on a bare host and wrong in a container:
a process bound to loopback inside the container's own network namespace is
unreachable through a published port, so the UI would simply not work. In the
container the process binds 0.0.0.0 and the guarantee comes from the publish
spec -- `127.0.0.1:8765:8765` in `docker-compose.yml` -- exactly as the existing
`serve --http` path already documents. That makes the Host allowlist below the
real in-container backstop rather than only a rebinding defence, so it is not
optional. The CLI still *defaults* to 127.0.0.1, because the safe case should be
the one you get by typing nothing.
"""

from __future__ import annotations

import logging
import secrets
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import pages

logger = logging.getLogger(__name__)

SESSION_COOKIE = "garmin_web_session"
DEFAULT_PORT = 8765

# Bounded so a stream of GET /login cannot grow this without limit. Far larger
# than one operator needs and far smaller than a memory problem.
_MAX_SESSIONS = 32

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_SECURITY_HEADERS = {
    # The pages carry a CSRF token; a cached copy is a stale token at best.
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    # `same-origin`, NOT `no-referrer`, and this is load-bearing rather than a
    # preference. Per Fetch, a page whose referrer policy is `no-referrer`
    # sends `Origin: null` on a form POST and no `Referer` at all -- so
    # `no-referrer` makes the same-origin check below unsatisfiable and the
    # login form refuses its own submission. Observed in a real browser; no
    # TestClient test could have found it, because TestClient does not compute
    # `Origin` the way a browser does.
    #
    # `same-origin` sends the real Origin and a full Referer for our own
    # requests, and neither for anyone else's -- which is exactly the
    # distinction the check is trying to make. Nothing leaks: CSP is
    # `default-src 'none'`, so there are no third-party requests to leak to.
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}


def default_allowed_hosts(port: int = DEFAULT_PORT) -> tuple[str, ...]:
    return (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")


class CsrfStore:
    """Session id -> CSRF token, in memory.

    Deliberately not a signed double-submit cookie. §7a asks for a token *bound
    to the session*, and one process serving one operator on demand has no
    reason to reach for statelessness it cannot benefit from.
    """

    def __init__(self) -> None:
        self._tokens: OrderedDict[str, str] = OrderedDict()

    def issue(self) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(32)
        token = secrets.token_urlsafe(32)
        self._tokens[session_id] = token
        while len(self._tokens) > _MAX_SESSIONS:
            self._tokens.popitem(last=False)
        return session_id, token

    def valid(self, session_id: str | None, token: str | None) -> bool:
        if not session_id or not token:
            return False
        expected = self._tokens.get(session_id)
        if expected is None:
            return False
        # Constant-time: a token check that leaks its answer through timing is
        # not a token check.
        return secrets.compare_digest(expected, token)


class LoopbackGuard(BaseHTTPMiddleware):
    """Host allowlist, same-origin enforcement, and security headers.

    One middleware rather than three, for the same reason `_garmin_access` is
    one doorway rather than two: a request that reaches a route has passed every
    check, and there is no ordering to get wrong later.

    **No CORS headers are emitted anywhere, ever.** Not by omission -- there is
    no code here that could add one. `Access-Control-Allow-Origin` on a page
    that mints credentials would hand every site in the browser exactly what the
    Host and Origin checks exist to deny.
    """

    def __init__(self, app, allowed_hosts: tuple[str, ...]) -> None:
        super().__init__(app)
        self._allowed = frozenset(h.lower() for h in allowed_hosts)
        self._origins = frozenset(f"http://{h.lower()}" for h in allowed_hosts)

    async def dispatch(self, request: Request, call_next):
        host = (request.headers.get("host") or "").lower()
        if host not in self._allowed:
            # Refused before routing, so a rebound hostname never reaches a
            # handler -- not even a 404 handler, which would still confirm the
            # server is here.
            logger.warning("rejected request with Host header %r", host)
            return PlainTextResponse(
                "Refused: unexpected Host header. This server answers only on "
                + ", ".join(sorted(self._allowed))
                + ".",
                status_code=403,
            )

        if request.method not in _SAFE_METHODS and not self._same_origin(request):
            logger.warning("rejected cross-origin %s %s", request.method, request.url.path)
            return PlainTextResponse(
                "Refused: this request did not come from this page.",
                status_code=403,
            )

        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers[header] = value
        return response

    def _same_origin(self, request: Request) -> bool:
        """Browsers send `Origin` on form POSTs, same-origin ones included.

        A state-changing request carrying neither `Origin` nor `Referer` is
        refused rather than trusted. On a form that exchanges a password it is
        the right default, and the operator's own browser always sends one.
        """
        origin = request.headers.get("origin")
        if origin is not None:
            # `null` is a real value a browser sends -- from a sandboxed iframe,
            # a cross-origin redirect, or a `no-referrer` page. It is never us,
            # and the allowlist contains no entry it could match. Checked
            # explicitly so that stays true if the allowlist is ever built from
            # something less careful.
            if origin.lower() == "null":
                return False
            return origin.lower() in self._origins
        referer = request.headers.get("referer")
        if referer is not None:
            return any(referer.lower().startswith(o + "/") or referer.lower() == o
                       for o in self._origins)
        return False


def _mode_of(path: Path) -> str:
    return format(path.stat().st_mode & 0o777, "04o")


def default_status_rows() -> list[tuple[str, str, str]]:
    """What the status page reports. No Garmin call, no token contents.

    Everything here is read from the local filesystem and the environment, which
    is what makes the page free to reload. The one question it deliberately does
    not answer is "do these tokens still work" -- that costs a request, and
    `garmin_auth_status` already owns it.
    """
    from ..source import ratelimit
    from ..source.client import tokenstore_path
    from ..source.lock import lock_path_for, probe_locking

    tokens = Path(tokenstore_path())
    rows: list[tuple[str, str, str]] = [("Token file", str(tokens), "")]

    if tokens.is_file():
        stat = tokens.stat()
        written = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        mode = _mode_of(tokens)
        rows += [
            ("Tokens", "present", "good"),
            ("Written (UTC)", written.isoformat(timespec="seconds"), ""),
            # 0600 is a requirement, not a detail (§4), so it is shown and
            # flagged when it is wrong rather than silently accepted.
            ("Permissions", mode, "good" if mode == "0600" else "bad"),
        ]
    else:
        rows.append(("Tokens", "none — sign in below", "bad"))

    ok, detail = probe_locking(str(tokens))
    rows += [
        ("Lock file", str(lock_path_for(str(tokens))), ""),
        # Probed by taking a real lock, not by checking that fcntl imports:
        # it is the volume that varies, not the platform.
        ("flock", "supported" if ok else f"NOT SUPPORTED — {detail}",
         "good" if ok else "bad"),
    ]

    budget = ratelimit.Budget.from_env()
    rows.append(
        ("Request budget", f"{budget.rate_per_sec:g} req/s, burst {budget.burst:g}", "")
    )
    return rows


class MfaRequired(Exception):
    """Garmin asked for a code and this surface cannot collect one."""


def _default_bootstrap(email: str, password: str) -> str:
    """Exchange the password for tokens through the one function that may.

    The MFA callback records that it was asked before raising, and the answer is
    read back from that flag rather than from the exception type. That looks
    like belt and braces and is not: raising from `prompt_mfa` does NOT reach
    us as itself. garminconnect calls the callback deep inside its login
    strategy loop, nothing there re-raises our type, and `bootstrap_login` ends
    in a broad `except Exception` that hands everything to `_translate` -- which
    knows only the library's vocabulary and maps an unrecognised exception onto
    `SourceUnavailable(str(exc))`. `str(MfaRequired())` is the empty string, so
    catching the type would have produced "Could not reach Garmin Connect: "
    and never the page naming the CLI, in the one situation where naming it is
    the whole point (§4).
    """
    from ..source.client import bootstrap_login
    from ..source.errors import GarminSourceError

    asked_for_a_code = False

    def prompt_mfa() -> str:
        nonlocal asked_for_a_code
        asked_for_a_code = True
        # The web flow has no way to collect a code mid-request, so it must not
        # pretend to. Raising turns "this request hangs forever" into an error.
        raise MfaRequired()

    try:
        return bootstrap_login(email=email, password=password, prompt_mfa=prompt_mfa)
    except GarminSourceError:
        if asked_for_a_code:
            raise MfaRequired() from None
        raise


def _default_version() -> str:
    from ..server import _server_version

    return _server_version()


def create_app(
    *,
    allowed_hosts: tuple[str, ...] | None = None,
    bootstrap: Callable[[str, str], str] | None = None,
    status_rows: Callable[[], list[tuple[str, str, str]]] | None = None,
    version: Callable[[], str] | None = None,
) -> Starlette:
    """Build the app. Every seam that touches Garmin or the disk is injectable,
    so the security properties above are tested without a token file, without a
    network, and without spending a single request against an account that has
    already been rate limited once."""
    allowed_hosts = allowed_hosts or default_allowed_hosts()
    bootstrap = bootstrap or _default_bootstrap
    status_rows = status_rows or default_status_rows
    version = version or _default_version

    csrf = CsrfStore()

    async def status(request: Request) -> Response:
        return HTMLResponse(pages.status_page(status_rows(), version()))

    async def login_form(request: Request) -> Response:
        session_id, token = csrf.issue()
        response = HTMLResponse(pages.login_page(token))
        # No `secure`: this is plain HTTP on loopback by design, and a Secure
        # cookie would simply never be sent back.
        response.set_cookie(
            SESSION_COOKIE, session_id, httponly=True, samesite="strict", path="/"
        )
        return response

    async def login_submit(request: Request) -> Response:
        form = await request.form()
        session_id = request.cookies.get(SESSION_COOKIE)
        if not csrf.valid(session_id, str(form.get("csrf_token") or "")):
            return PlainTextResponse(
                "Refused: stale or missing form token. Reload the page and try again.",
                status_code=403,
            )

        email = str(form.get("email") or "").strip()
        password = str(form.get("password") or "")

        def _rerender(error: str, *, disable: bool = False, advice: str = "") -> Response:
            new_session, new_token = csrf.issue()
            # The email comes back so the operator does not retype it. The
            # password never does -- a helpfully repopulated password field puts
            # it in the page source and the browser's back/forward cache.
            response = HTMLResponse(
                pages.login_page(
                    new_token, email=email, error=error,
                    disable_submit=disable, advice=advice,
                ),
                status_code=400,
            )
            response.set_cookie(
                SESSION_COOKIE, new_session, httponly=True, samesite="strict", path="/"
            )
            return response

        if not email or not password:
            return _rerender("Email and password are both required.")

        from ..source.errors import GarminSourceError, RateLimited

        try:
            who = bootstrap(email, password)
        except MfaRequired:
            return _rerender(
                "Garmin asked for an MFA code.", disable=True, advice=pages.MFA_ADVICE
            )
        except RateLimited:
            # Disabled, not merely warned about. §12.1: the failure mode here is
            # an operator pressing the button again, and each press makes the
            # block longer.
            return _rerender(
                "Garmin is rate limiting this IP (HTTP 429).",
                disable=True,
                advice=pages.RATE_LIMIT_ADVICE,
            )
        except GarminSourceError as exc:
            # str(exc) is our own errors.py vocabulary, written to be read by a
            # human. It never contains the password: the library sets
            # self.password = None and our messages are built from paths only.
            return _rerender(str(exc), disable=True, advice=pages.RATE_LIMIT_ADVICE)
        finally:
            del password

        return HTMLResponse(pages.success_page(who))

    return Starlette(
        routes=[
            Route("/", status, methods=["GET"]),
            Route("/login", login_form, methods=["GET"]),
            Route("/login", login_submit, methods=["POST"]),
        ],
        middleware=[Middleware(LoopbackGuard, allowed_hosts=allowed_hosts)],
    )
