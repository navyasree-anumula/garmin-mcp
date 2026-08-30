"""The lock has to work BETWEEN processes, so it is tested between processes.

An in-process test of a cross-process lock proves nothing: `threading.Lock`
would pass it, and `threading.Lock` is exactly the thing garminconnect already
has and which does not help us.
"""

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from garmin_mcp.source.lock import (
    LOCK_FILENAME,
    LockTimeout,
    file_lock,
    garmin_lock,
    lock_path_for,
)

CHILD = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, {repo!r})
    from garmin_mcp.source.lock import garmin_lock
    tokens, marker, hold = sys.argv[1], sys.argv[2], float(sys.argv[3])
    with garmin_lock(tokens, timeout=30):
        with open(marker, "a") as fh:
            fh.write(f"enter {{time.time():.4f}}\\n")
        time.sleep(hold)
        with open(marker, "a") as fh:
            fh.write(f"exit  {{time.time():.4f}}\\n")
    """
).format(repo=str(Path(__file__).resolve().parent.parent))


def _spawn(tokens, marker, hold):
    return subprocess.Popen([sys.executable, "-c", CHILD, str(tokens), str(marker), str(hold)])


def test_lock_file_sits_beside_the_token_file_not_on_it():
    """`dump()` replaces the token file, giving it a new inode. flock binds to
    the open file description, so a lock held on that path would survive as a
    lock on an unlinked inode -- healthy-looking and excluding nobody."""
    path = lock_path_for("/data/garmin_tokens.json")

    assert path.name == LOCK_FILENAME
    assert path != Path("/data/garmin_tokens.json")
    assert path.parent == Path("/data")


def test_reentrant_within_a_process(tmp_path):
    """flock is per open file description, so a naive second acquire in the same
    process would block on itself forever."""
    tokens = tmp_path / "garmin_tokens.json"

    with garmin_lock(str(tokens), timeout=5):
        with garmin_lock(str(tokens), timeout=5):
            pass  # reaching here at all is the assertion


def test_two_processes_do_not_overlap(tmp_path):
    tokens = tmp_path / "garmin_tokens.json"
    marker = tmp_path / "events.txt"

    a = _spawn(tokens, marker, 0.6)
    time.sleep(0.15)  # let A win the race deterministically
    b = _spawn(tokens, marker, 0.1)
    assert a.wait(timeout=30) == 0
    assert b.wait(timeout=30) == 0

    events = marker.read_text().split()
    kinds = events[0::2]
    # Strictly alternating enter/exit means the critical sections never
    # interleaved. An overlap would read enter, enter, exit, exit.
    assert kinds == ["enter", "exit", "enter", "exit"], marker.read_text()


def test_waiting_process_actually_waited(tmp_path):
    """The other half: prove the second process was blocked, not merely lucky."""
    tokens = tmp_path / "garmin_tokens.json"
    marker = tmp_path / "events.txt"

    a = _spawn(tokens, marker, 0.6)
    time.sleep(0.15)
    b = _spawn(tokens, marker, 0.0)
    a.wait(timeout=30)
    b.wait(timeout=30)

    times = [float(t) for t in marker.read_text().split()[1::2]]
    first_exit, second_enter = times[1], times[2]

    assert second_enter >= first_exit - 0.01, "second process entered too early"


def test_timeout_raises_rather_than_hanging(tmp_path):
    """A tool call that blocks forever is worse than one that fails."""
    tokens = tmp_path / "garmin_tokens.json"
    marker = tmp_path / "events.txt"

    holder = _spawn(tokens, marker, 2.0)
    time.sleep(0.3)
    try:
        with pytest.raises(LockTimeout) as excinfo:
            with garmin_lock(str(tokens), timeout=0.4):
                pass
        assert "Timed out" in str(excinfo.value)
    finally:
        holder.wait(timeout=30)


def test_file_lock_is_not_reentrant_by_design(tmp_path):
    """Documents the distinction: `file_lock` is the raw primitive and would
    deadlock on itself, which is why `garmin_lock` counts depth instead."""
    lock = tmp_path / "x.lock"

    with file_lock(lock, timeout=1):
        with pytest.raises(LockTimeout):
            with file_lock(lock, timeout=0.2):
                pass
