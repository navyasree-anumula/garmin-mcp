"""The lockfile is what ships. Nothing dev-only belongs in it.

`Dockerfile` runs `pip install -r requirements.lock`, so every line here lands in
the production image. Until 2026-08-30 that included pytest and its dependency
tree -- a test runner inside the runtime container, and a supply-chain surface
docs/SCOPE.md §8 exists to keep out. It was found by adding `pip-audit` to CI,
which flagged a pytest CVE in an image that should never have had pytest at all.

Regenerating the lock from a dev environment reintroduces this silently, so it
gets a test.
"""

from pathlib import Path

import pytest

LOCK = Path(__file__).resolve().parent.parent / "requirements.lock"

# Dev-only roots and the transitive deps they drag in.
DEV_ONLY = {"pytest", "pluggy", "iniconfig", "pygments", "pip-audit", "vcrpy"}


def _locked_names() -> set[str]:
    names = set()
    for line in LOCK.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.add(line.split("==")[0].strip().lower())
    return names


def test_lock_is_readable_and_non_empty():
    """Guards the guard: an unreadable lock would make every other assertion
    below pass vacuously."""
    assert _locked_names(), f"no pinned packages parsed from {LOCK}"


def test_lock_carries_no_dev_only_packages():
    leaked = _locked_names() & DEV_ONLY

    assert not leaked, (
        f"dev-only packages in the runtime lockfile: {sorted(leaked)}. "
        "requirements.lock is installed into the production image; regenerate "
        "it from a runtime-only environment."
    )


def test_lock_still_carries_the_runtime_roots():
    """The other direction: a lock stripped too far would also pass the test
    above."""
    names = _locked_names()

    for required in ("garminconnect", "mcp", "curl-cffi", "pydantic"):
        assert required in names or required.replace("-", "_") in names, (
            f"{required} missing from the lockfile"
        )


def test_every_line_is_exactly_pinned():
    """§8 requires exact pins: curl_cffi is a binary wheel whose purpose is TLS
    impersonation, so an unpinned rebuild is a real exposure."""
    for line in LOCK.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            assert "==" in line, f"not exactly pinned: {line!r}"
