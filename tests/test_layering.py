"""§11: `garminconnect` is imported in exactly one package.

This rule existed from day one with nothing enforcing it, and until now nothing
pressed on it -- `server.py` was the only adapter, so "consistency" had no
second case to be consistent with. `web/` is that second case, which makes this
the moment the rule either holds or quietly stops being true.

What it buys: a future source swap -- the official API, a fork, a different
library -- touches one package instead of being spread across every adapter that
found it convenient to reach past the boundary.
"""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "garmin_mcp"

ADAPTER_MODULES = sorted(
    path
    for path in PACKAGE.rglob("*.py")
    # source/ is the one package allowed to import it; that is the rule, not an
    # exception to it.
    if "source" not in path.relative_to(PACKAGE).parts
)


def imported_names(path: Path) -> set[str]:
    """Every module named by an import, at any nesting depth.

    Walks the AST rather than grepping, so imports inside functions are caught
    too -- `cli.py` and `web/app.py` both import lazily, and a grep for a
    top-level import line would report a clean bill of health for a file that
    reaches past the boundary in every handler.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_there_are_adapter_modules_to_check():
    """Guards the guard: a glob that silently matched nothing would make every
    assertion below vacuously true."""
    assert len(ADAPTER_MODULES) >= 4
    assert any(path.name == "server.py" for path in ADAPTER_MODULES)
    assert any(path.parent.name == "web" for path in ADAPTER_MODULES)


@pytest.mark.parametrize("path", ADAPTER_MODULES, ids=lambda p: p.name)
def test_no_adapter_imports_garminconnect(path):
    offending = {
        name for name in imported_names(path)
        if name == "garminconnect" or name.startswith("garminconnect.")
    }

    assert not offending, (
        f"{path.relative_to(PACKAGE.parent)} imports {sorted(offending)}. "
        "Only garmin_mcp/source/ may import garminconnect (docs/SCOPE.md §11)."
    )


def test_the_check_can_actually_fail(tmp_path):
    """A layering test that has never been seen to fire is decoration."""
    offender = tmp_path / "offender.py"
    offender.write_text(
        "def f():\n"
        "    from garminconnect import Garmin\n"
        "    return Garmin\n"
    )

    assert "garminconnect" in imported_names(offender)


def test_source_really_is_the_module_that_imports_it():
    """The mirror image: if nothing imported garminconnect anywhere, the test
    above would pass for the wrong reason."""
    client = PACKAGE / "source" / "client.py"

    assert "garminconnect" in imported_names(client)
