"""`selftest` is the only way to check the cross-process claims in the real
deployment, so it needs to work and to fail honestly."""

import errno

import pytest

from garmin_mcp.cli import main
from garmin_mcp.source import lock as lockmod


@pytest.fixture(autouse=True)
def fast_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMINTOKENS", str(tmp_path / "garmin_tokens.json"))
    monkeypatch.setenv("GARMIN_RATE_PER_SEC", "1000")
    monkeypatch.setenv("GARMIN_RATE_BURST", "1000")


def test_selftest_succeeds_and_emits_one_grant_per_request(capsys):
    rc = main(["selftest", "--label", "X", "--count", "3"])

    out = capsys.readouterr()
    grants = [ln for ln in out.out.splitlines() if ln.startswith("GRANT ")]
    assert rc == 0
    assert len(grants) == 3
    assert all(ln.split()[1] == "X" for ln in grants)
    # Timestamps must parse -- two containers' output gets merged and sorted.
    assert all(float(ln.split()[3]) > 0 for ln in grants)


def test_selftest_needs_no_token_file(capsys):
    """It must be runnable before bootstrap, and on a volume with no tokens."""
    rc = main(["selftest", "--count", "1"])

    assert rc == 0


def test_selftest_fails_loudly_when_locking_is_unavailable(capsys, monkeypatch):
    def raise_enolck(fd, op):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(lockmod.fcntl, "flock", raise_enolck)

    rc = main(["selftest", "--count", "2"])

    out = capsys.readouterr()
    assert rc == 1
    assert "NOT SUPPORTED" in out.err
    assert "not active on this volume" in out.err
    # It must not pretend to have tested a budget it never exercised.
    assert "GRANT" not in out.out


def test_selftest_makes_no_garmin_calls(monkeypatch, capsys):
    """The point of the command: safe to run repeatedly against a live account."""
    import garmin_mcp.source.client as client

    def explode(*a, **k):
        raise AssertionError("selftest touched Garmin")

    monkeypatch.setattr(client, "Garmin", explode)

    assert main(["selftest", "--count", "2"]) == 0
