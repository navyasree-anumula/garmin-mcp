"""Entrypoints: `login` (interactive bootstrap) and `serve` (the MCP server).

The password is entered here and nowhere else. It is never an argv value, never
an environment variable, never an MCP tool argument (docs/SCOPE.md §4).
"""

from __future__ import annotations

import argparse
import logging
import sys
from getpass import getpass

from .source.errors import GarminSourceError, RateLimited


def _configure_logging() -> None:
    """All logging goes to stderr, always.

    Under stdio transport stdout carries JSON-RPC. A single stray byte written
    there corrupts the stream and breaks every tool at once, silently.
    """
    # force=True matters and is not boilerplate. basicConfig is a NO-OP when the
    # root logger already has handlers, so without it a logging setup performed
    # by any earlier import would stand and our level would silently not apply.
    # That level is the only thing keeping health data out of the logs:
    # garminconnect's _run_request logs the full response body at DEBUG
    # ("API error response: status=%s body=%r"), and on a health endpoint that
    # body is health data (docs/SCOPE.md §8).
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _cmd_login(_args: argparse.Namespace) -> int:
    from .source import client as source

    print("Garmin Connect bootstrap. Credentials are used once and not stored.", file=sys.stderr)
    email = input("Garmin email: ").strip()
    password = getpass("Garmin password: ")

    try:
        who = source.bootstrap_login(
            email=email,
            password=password,
            prompt_mfa=lambda: input("MFA code: ").strip(),
        )
    except RateLimited as exc:
        print(f"\n{exc}", file=sys.stderr)
        print(
            "WAIT — do not retry. Repeated attempts extend the block and a captcha "
            "lockout has no workaround.",
            file=sys.stderr,
        )
        return 2
    except GarminSourceError as exc:
        print(f"\nBootstrap failed: {exc}", file=sys.stderr)
        print(
            "If this was a captcha or bot challenge, WAIT — do not retry. Retrying "
            "makes it worse and there is no solver by design.",
            file=sys.stderr,
        )
        return 1
    finally:
        del password

    print(f"\nTokens written. Authenticated as: {who}", file=sys.stderr)
    print(
        "Back up the token volume now — if a captcha ever blocks a fresh login, "
        "this file is the only way back.",
        file=sys.stderr,
    )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .server import mcp

    if args.http:
        # Binding 0.0.0.0 INSIDE the container is correct; exposure is controlled
        # at the Docker layer with `-p 127.0.0.1:3001:3001`. Publishing this as
        # `-p 3001:3001` would serve health data to every network the host joins,
        # with no authentication (docs/SCOPE.md §7).
        mcp.run(transport="streamable-http", host="0.0.0.0", port=args.port)  # noqa: S104
    else:
        mcp.run()
    return 0


def _cmd_selftest(args: argparse.Namespace) -> int:
    """Exercise the cross-process machinery without touching Garmin.

    Everything this command uses -- the lock file and the request budget -- lives
    beside the token file on the shared volume, and none of it needs the token
    file to exist. So this runs before bootstrap, and more importantly it can be
    run in two containers at once to prove that the budget really is global and
    the lock really does exclude. That claim is untestable from inside a single
    process, and it is the entire reason both modules keep state on disk.
    """
    import time

    from .source import ratelimit
    from .source.client import tokenstore_path
    from .source.lock import lock_path_for, probe_locking

    tokens = tokenstore_path()
    directory = lock_path_for(tokens).parent
    budget = ratelimit.Budget.from_env()
    ok, detail = probe_locking(tokens)

    print("garmin-mcp selftest — no Garmin API calls are made.\n", file=sys.stderr)
    print(f"  tokens path : {tokens}", file=sys.stderr)
    print(f"  lock file   : {lock_path_for(tokens)}", file=sys.stderr)
    print(f"  budget file : {directory / ratelimit.STATE_FILENAME}", file=sys.stderr)
    print(f"  flock       : {'SUPPORTED' if ok else 'NOT SUPPORTED'} — {detail}", file=sys.stderr)
    print(
        f"  budget      : {budget.rate_per_sec:g} req/s, burst {budget.burst:g}\n",
        file=sys.stderr,
    )

    if not ok:
        print(
            "FAIL: cross-process protection is not active on this volume.",
            file=sys.stderr,
        )
        return 1

    from .source.client import _garmin_access

    started = time.time()
    for n in range(1, args.count + 1):
        with _garmin_access(tokens):
            # GRANT lines go to stdout, one per line, so the output of two
            # containers can be concatenated and sorted by timestamp.
            print(f"GRANT {args.label} {n} {time.time():.5f}", flush=True)
    elapsed = time.time() - started

    print(
        f"\n  {args.label}: {args.count} grants in {elapsed:.2f}s", file=sys.stderr
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()

    parser = argparse.ArgumentParser(prog="garmin-mcp")
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="One-time interactive bootstrap. Needs a TTY.")
    p_login.set_defaults(func=_cmd_login)

    p_serve = sub.add_parser("serve", help="Run the MCP server. Defaults to stdio.")
    p_serve.add_argument(
        "--http",
        action="store_true",
        help="Serve Streamable HTTP instead of stdio. Publish to loopback only.",
    )
    p_serve.add_argument("--port", type=int, default=3001)
    p_serve.set_defaults(func=_cmd_serve)

    p_selftest = sub.add_parser(
        "selftest",
        help="Exercise the lock and request budget. Makes no Garmin calls.",
    )
    p_selftest.add_argument("--label", default="A", help="Tag for the GRANT lines.")
    p_selftest.add_argument("--count", type=int, default=4)
    p_selftest.set_defaults(func=_cmd_selftest)

    args = parser.parse_args(argv)
    return int(args.func(args))
