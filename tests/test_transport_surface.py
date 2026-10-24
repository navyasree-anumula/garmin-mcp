"""One claim: no module asks `mcp.run` for a transport.

`docs/SCOPE.md` §7 has recorded stdio as the transport decision since v1.2, and
until 2026-10-24 the code disagreed: `serve --http` ran
`mcp.run(transport="streamable-http", host="0.0.0.0", ...)`, an unauthenticated
listener for special-category health data that could be started by typing one
word. The flag is gone. This is what stops it returning by accident.

A separate file because each guard file here owns exactly one claim --
`test_layering.py` owns the import boundary, `test_fetch_seam.py` owns the single
doorway.

Why an AST walk and not a grep for "--http": the flag is gone, so a grep has
nothing left to match. The thing worth forbidding is the *call*, and the call can
be reintroduced without ever using that spelling again.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "garmin_mcp"

MODULES = sorted(PACKAGE.rglob("*.py"))


def run_calls_with_transport(tree: ast.AST) -> list[str]:
    """Every `.run(...)` call in `tree` that passes a `transport` keyword.

    Matched on the attribute name rather than the receiver, so `mcp.run`,
    `server.run` and `self._mcp.run` are all caught -- renaming the variable is
    exactly how this would come back and look innocent in review.
    """
    offending: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "run":
            continue
        if any(kw.arg == "transport" for kw in node.keywords):
            offending.append(f"line {node.lineno}")
    return offending


def test_no_module_asks_mcp_run_for_a_transport():
    # Guards the guard: a glob that silently matched nothing would make the loop
    # below pass without having checked anything at all.
    assert len(MODULES) >= 5, f"package glob found too little: {MODULES}"
    assert any(path.name == "cli.py" for path in MODULES)

    offenders: dict[str, list[str]] = {}
    for path in MODULES:
        hits = run_calls_with_transport(ast.parse(path.read_text()))
        if hits:
            offenders[str(path.relative_to(PACKAGE.parent))] = hits

    assert not offenders, (
        f"{offenders} pass a transport to .run(). This server speaks stdio only "
        "(docs/SCOPE.md §7), so mcp.run() takes no transport argument here."
    )


def test_the_transport_check_can_actually_fail(tmp_path):
    """A guard never observed to fire is decoration."""
    offender = tmp_path / "offender.py"
    offender.write_text(
        "def serve():\n"
        "    mcp.run(transport='streamable-http', host='0.0.0.0', port=3001)\n"
    )

    assert run_calls_with_transport(ast.parse(offender.read_text()))

    # The mirror image: the one call the server IS supposed to make must not trip
    # it, or the guard would forbid serving at all and still look green.
    allowed = tmp_path / "allowed.py"
    allowed.write_text("def serve():\n    mcp.run()\n")

    assert not run_calls_with_transport(ast.parse(allowed.read_text()))
