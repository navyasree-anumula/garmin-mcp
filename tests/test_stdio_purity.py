"""stdout must carry JSON-RPC and nothing else.

Under stdio transport a single stray byte on stdout corrupts the stream and
breaks every tool at once, with no useful error. garminconnect is clean (no
print() calls, module-level logger, never touches sys.stdout) but our own code
has to stay clean too, so this asserts it rather than trusting it.
"""

import json
import os
import subprocess
import sys

INITIALIZE = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "purity-probe", "version": "1.0"},
        },
    }
)


def stdout_is_pure_jsonrpc(text: str) -> bool:
    """True only if every non-empty line is a JSON-RPC object."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    for line in lines:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return False
    return True


def _run(code_prefix: str = "") -> subprocess.CompletedProcess:
    """Run the server over stdio, optionally with something printed first."""
    program = (
        f"{code_prefix}"
        "import sys;from garmin_mcp.cli import main;sys.argv=['garmin-mcp','serve'];"
        "sys.exit(main())"
    )
    # Inherit the real environment so the child resolves its interpreter and
    # site-packages the same way pytest did -- replacing it wholesale breaks on
    # runners whose Python lives outside /usr. GARMINTOKENS is overridden rather
    # than cleared so a developer's real token file can never leak into a test.
    env = dict(os.environ)
    env["GARMINTOKENS"] = "/nonexistent/tokens.json"

    return subprocess.run(
        [sys.executable, "-c", program],
        input=INITIALIZE + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_server_stdout_is_only_jsonrpc():
    result = _run()

    assert result.stdout.strip(), "server produced no stdout at all"
    assert stdout_is_pure_jsonrpc(result.stdout), (
        f"stdout was not pure JSON-RPC:\n{result.stdout!r}"
    )


def test_server_starts_without_tokens():
    """Missing tokens must not stop the server from serving. A client that cannot
    complete the handshake cannot even call garmin_auth_status to find out why."""
    result = _run()

    reply = json.loads(result.stdout.splitlines()[0])
    assert reply["id"] == 1
    assert "result" in reply, f"handshake failed: {reply}"
    assert reply["result"]["serverInfo"]["name"] == "garmin"


def test_the_purity_check_can_actually_fail():
    """Validate the instrument in both directions.

    A check that has never been shown to fail is not a check. This pollutes
    stdout deliberately and asserts the detector catches it.
    """
    result = _run(code_prefix="print('this byte breaks the protocol');")

    assert not stdout_is_pure_jsonrpc(result.stdout), (
        "the purity check passed on deliberately polluted stdout — it is vacuous"
    )
