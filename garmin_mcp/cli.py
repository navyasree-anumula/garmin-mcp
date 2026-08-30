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
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
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

    args = parser.parse_args(argv)
    return int(args.func(args))
