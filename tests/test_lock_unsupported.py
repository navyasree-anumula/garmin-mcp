"""A filesystem that cannot lock must say so, not blame a phantom process.

flock signals contention and incapacity through the same exception base. The
first version of this module caught bare OSError and treated both as "someone
else holds it", which meant a volume with no advisory locking would poll for the
full timeout and then report `LockTimeout` -- "another instance of this server is
busy". Precise, actionable, and wrong: it sends the operator hunting a container
that does not exist, while the real answer is that cross-process protection was
never active at all.

That distinction matters most on a Docker named volume, which is exactly where
this runs and exactly where flock support is worth verifying rather than
assuming.
"""

import errno
import time

import pytest

from garmin_mcp.source import lock as lockmod
from garmin_mcp.source.lock import (
    LockTimeout,
    LockUnsupported,
    file_lock,
    garmin_lock,
    probe_locking,
)


@pytest.fixture
def flock_unsupported(monkeypatch):
    """Stand in for a volume driver with no advisory locking."""

    def raise_enolck(fd, op):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(lockmod.fcntl, "flock", raise_enolck)


def test_unsupported_filesystem_is_not_reported_as_contention(
    tmp_path, flock_unsupported
):
    with pytest.raises(LockUnsupported) as excinfo:
        with garmin_lock(str(tmp_path / "garmin_tokens.json"), timeout=5):
            pass

    message = str(excinfo.value)
    assert "does not support advisory locking" in message
    assert "NOT active" in message
    # The wrong answer must not be given.
    assert "busy" not in message


def test_unsupported_fails_fast_rather_than_polling(tmp_path, flock_unsupported):
    """Spinning to the deadline would waste 30s to reach a wrong conclusion."""
    t0 = time.monotonic()

    with pytest.raises(LockUnsupported):
        with file_lock(tmp_path / "x.lock", timeout=5):
            pass

    assert time.monotonic() - t0 < 0.5


def test_real_contention_still_reports_a_timeout(tmp_path):
    """The other half: with a working filesystem, contention must still be
    contention. A fix that turned every failure into LockUnsupported would pass
    the tests above."""
    lock = tmp_path / "x.lock"

    with file_lock(lock, timeout=1):
        with pytest.raises(LockTimeout) as excinfo:
            with file_lock(lock, timeout=0.2):
                pass

    assert not isinstance(excinfo.value, LockUnsupported)


def test_probe_reports_supported_on_a_working_filesystem(tmp_path):
    ok, detail = probe_locking(str(tmp_path / "garmin_tokens.json"))

    assert ok is True
    assert "supported" in detail


def test_probe_reports_unsupported_and_says_why(tmp_path, flock_unsupported):
    ok, detail = probe_locking(str(tmp_path / "garmin_tokens.json"))

    assert ok is False
    assert "No locks available" in detail


def test_probe_actually_takes_a_lock(tmp_path, monkeypatch):
    """Guards the guard. A probe that returned True without locking anything
    would be worse than no probe -- it would certify a volume it never tested."""
    calls = []
    real = lockmod.fcntl.flock

    def spy(fd, op):
        calls.append(op)
        return real(fd, op)

    monkeypatch.setattr(lockmod.fcntl, "flock", spy)

    probe_locking(str(tmp_path / "garmin_tokens.json"))

    assert calls, "probe_locking reported a verdict without calling flock"
