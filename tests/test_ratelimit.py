"""The budget has to be shared BETWEEN processes, so that is what is tested.

A bucket in process memory would pass every single-process test here while
permitting exactly double the configured rate in the real deployment, because
Claude Desktop runs two containers against one token volume. That is the whole
reason this module keeps state in a file, so the sharing is what gets proved.
"""

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from garmin_mcp.source.ratelimit import (
    STATE_FILENAME,
    Budget,
    RateLimitTimeout,
    acquire,
)

REPO = str(Path(__file__).resolve().parent.parent)


def min_window_seconds(n: int, rate: float, burst: float) -> float:
    """Lower bound on the span from the first grant to the last, for n grants.

    One burst token is free and the remaining `n - burst` are each paid for at
    `rate`, so no conforming limiter can fit n grants into less than this. Pure
    so the bound can be asserted on its own and shown to fail.
    """
    return max(0.0, (n - burst) / rate)


# Absorbs scheduling delay on the FIRST stamp, the only delay that SHRINKS the
# measured window -- a late last stamp only widens it. Deliberately well under
# the 0.5s bound it guards, so the assertion still bites: 0.40s effective,
# tighter than the undocumented 0.35s it replaces.
WINDOW_SLACK_S = 0.1

CHILD = textwrap.dedent(
    """
    import sys, time, os
    sys.path.insert(0, {repo!r})
    tokens, out, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    from garmin_mcp.source.ratelimit import acquire
    stamps = []
    for _ in range(n):
        acquire(tokens, timeout=60)
        stamps.append(time.time())
    with open(out, "w") as fh:
        fh.write("\\n".join(f"{{s:.5f}}" for s in stamps))
    """
).format(repo=REPO)


@pytest.fixture
def budget(monkeypatch):
    """Fast enough to keep the suite quick, slow enough to measure."""
    monkeypatch.setenv("GARMIN_RATE_PER_SEC", "10")
    monkeypatch.setenv("GARMIN_RATE_BURST", "1")
    return Budget.from_env()


def test_env_overrides_are_read(budget):
    assert budget.rate_per_sec == 10.0
    assert budget.burst == 1.0


def test_nonsense_env_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("GARMIN_RATE_PER_SEC", "banana")
    monkeypatch.setenv("GARMIN_RATE_BURST", "-3")

    b = Budget.from_env()

    assert b.rate_per_sec > 0
    assert b.burst > 0


def test_burst_is_spent_then_the_rate_applies(tmp_path, budget):
    tokens = tmp_path / "garmin_tokens.json"

    t0 = time.monotonic()
    acquire(str(tokens), timeout=30)  # the burst token, free
    first = time.monotonic() - t0
    acquire(str(tokens), timeout=30)  # must wait ~1/rate
    second = time.monotonic() - t0

    assert first < 0.05, "burst token should be immediate"
    assert second >= 0.08, "second request should have waited for a refill"


def test_two_processes_share_one_budget(tmp_path, budget):
    """The load-bearing test.

    Each child records when it was granted each request. Merging both children's
    timestamps and checking the spacing measures the GLOBAL rate directly, which
    is the property we actually care about -- and it does not depend on how the
    two processes happened to interleave.
    """
    tokens = tmp_path / "garmin_tokens.json"
    out_a, out_b = tmp_path / "a.txt", tmp_path / "b.txt"
    env = {**os.environ, "GARMIN_RATE_PER_SEC": "10", "GARMIN_RATE_BURST": "1"}

    procs = [
        subprocess.Popen(
            [sys.executable, "-c", CHILD, str(tokens), str(out), "3"], env=env
        )
        for out in (out_a, out_b)
    ]
    for p in procs:
        assert p.wait(timeout=60) == 0

    stamps = sorted(
        float(line)
        for out in (out_a, out_b)
        for line in out.read_text().splitlines()
        if line.strip()
    )
    assert len(stamps) == 6

    # Only the WIDEST window is asserted, never per-gap spacing. A child records
    # its stamp AFTER acquire returns (see CHILD above), so a stamp is grant time
    # plus an unbounded scheduling delay, and an adjacent gap carries the
    # DIFFERENCE of two such delays: two grants a real 0.1s apart read as 0.01s
    # if the earlier stamp was delayed 0.09s more than the later one. Across six
    # stamps only the first and last move the window at all, which is why this
    # bound survives what a per-gap assertion could not.
    window = stamps[-1] - stamps[0]
    floor = min_window_seconds(len(stamps), rate=10.0, burst=1.0)  # (6-1)/10 = 0.5
    assert window >= floor - WINDOW_SLACK_S, (
        f"global rate not enforced: six grants spanned {window:.3f}s, "
        f"need >= {floor - WINDOW_SLACK_S:.3f}s"
    )


def test_min_window_seconds_derives_the_bound_from_the_budget():
    """The bound is derived from the budget rather than guessed at."""
    assert min_window_seconds(6, rate=10.0, burst=1.0) == pytest.approx(0.5)
    # A single grant is the free burst token, so it costs no waiting.
    assert min_window_seconds(1, rate=10.0, burst=1.0) == pytest.approx(0.0)
    # A larger burst buys more free grants, so the floor drops.
    assert min_window_seconds(6, rate=10.0, burst=3.0) == pytest.approx(0.3)
    # Never negative: fewer grants than the burst allows waits for nothing.
    assert min_window_seconds(2, rate=10.0, burst=5.0) == 0.0


def test_the_rate_check_can_actually_fail():
    """Negative control: the comparison above has to reject stamps that break the
    bound, or asserting it proves nothing. Pure function, no subprocess."""
    floor = min_window_seconds(6, rate=10.0, burst=1.0)

    # Six grants crammed into 0.1s -- five times faster than the budget permits.
    too_fast = [0.0, 0.02, 0.04, 0.06, 0.08, 0.10]
    assert (too_fast[-1] - too_fast[0]) < floor - WINDOW_SLACK_S

    # A conforming spread still passes, so the bound is not unsatisfiable.
    conforming = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
    assert (conforming[-1] - conforming[0]) >= floor - WINDOW_SLACK_S


def test_corrupt_state_starts_full_rather_than_stalling(tmp_path, budget):
    """A garbled state file must not brick every Garmin call. Failing open is
    correct here: the cost is one unbudgeted burst, the alternative is a tool
    that never works again and gives no reason."""
    tokens = tmp_path / "garmin_tokens.json"
    (tmp_path / STATE_FILENAME).write_text("{not json at all")

    t0 = time.monotonic()
    acquire(str(tokens), timeout=5)

    assert time.monotonic() - t0 < 0.2


def test_clock_running_backwards_does_not_mint_tokens(tmp_path, budget):
    """State carries a wall-clock timestamp, and wall clocks move sideways."""
    tokens = tmp_path / "garmin_tokens.json"
    acquire(str(tokens), timeout=5)

    state = tmp_path / STATE_FILENAME
    data = json.loads(state.read_text())
    data["updated"] = time.time() + 3600  # an hour in the future
    state.write_text(json.dumps(data))

    t0 = time.monotonic()
    acquire(str(tokens), timeout=5)

    assert time.monotonic() - t0 >= 0.07, "future timestamp granted a free token"


def test_timeout_raises_rather_than_waiting_forever(tmp_path, monkeypatch):
    monkeypatch.setenv("GARMIN_RATE_PER_SEC", "0.01")
    monkeypatch.setenv("GARMIN_RATE_BURST", "1")
    tokens = tmp_path / "garmin_tokens.json"

    acquire(str(tokens), timeout=5)  # spend the burst

    with pytest.raises(RateLimitTimeout) as excinfo:
        acquire(str(tokens), timeout=0.3)

    assert "Narrow the request" in str(excinfo.value)
