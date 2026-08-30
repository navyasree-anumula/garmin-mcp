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

    # One burst token is free; the remaining five are paid for at 10/sec.
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    paid = sorted(gaps)[1:]  # drop the one free burst gap
    assert all(g >= 0.07 for g in paid), f"global rate not enforced: {gaps}"
    assert stamps[-1] - stamps[0] >= 0.35, "six requests came through too fast"


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
