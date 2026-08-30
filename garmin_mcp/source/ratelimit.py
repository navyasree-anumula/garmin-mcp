"""A client-side rate limit on Garmin requests (docs/SCOPE.md §6).

**The number here has no empirical basis and this module is not going to pretend
otherwise.** Garmin publishes no limits for this path. The default is chosen to
be obviously slower than a human tapping through the mobile app, on the
principle that the only lever we have on the §9 account-suspension risk is
request volume. Raise it against observed behaviour, never to make something
feel faster.

**Why the state lives in a file rather than in memory.** A bucket held in
process memory would be enforced once per process, and there are two processes:
Claude Desktop spawns two containers per configured stdio server (measured
2026-08-30, see `lock.py`). Two independent buckets means the real rate against
Garmin is double the configured one -- and a limiter that permits twice what it
claims is worse than none, because it invites trust it has not earned. The
bucket is therefore backed by a file on the shared token volume, so both
containers draw from the same allowance.

What this does NOT cover: refreshes and retries that garminconnect performs
inside `_run_request`, which we have no hook into. Those ride along with the
call that triggered them, so a request can cost more than one HTTP round trip.
The budget is deliberately low enough for that to be affordable.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import GarminSourceError
from .lock import file_lock

logger = logging.getLogger(__name__)

STATE_FILENAME = ".garmin.ratelimit.json"
LOCK_FILENAME = ".garmin.ratelimit.lock"

# One request every two seconds, with a little slack for a burst of four. An
# agent answering a question makes a handful of calls; this is invisible there
# and ruinous for anything resembling a scrape, which is the intent.
DEFAULT_RATE_PER_SEC = 0.5
DEFAULT_BURST = 4.0
DEFAULT_TIMEOUT_S = 30.0

_MAX_SLEEP_S = 1.0


class RateLimitTimeout(GarminSourceError):
    """Waited too long for a request budget."""

    def __init__(self, timeout: float) -> None:
        super().__init__(
            f"Timed out after {timeout:.0f}s waiting for the local Garmin request "
            f"budget. Too many requests were queued at once. Narrow the request "
            f"(a shorter date range, fewer metrics) rather than retrying."
        )


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("%s=%r must be positive; using %s", name, raw, default)
        return default
    return value


@dataclass(frozen=True)
class Budget:
    rate_per_sec: float
    burst: float

    @classmethod
    def from_env(cls) -> "Budget":
        return cls(
            rate_per_sec=_env_float("GARMIN_RATE_PER_SEC", DEFAULT_RATE_PER_SEC),
            burst=_env_float("GARMIN_RATE_BURST", DEFAULT_BURST),
        )


def _read_state(path: Path, budget: Budget) -> tuple[float, float]:
    """Return (tokens, updated_at). A missing or unreadable state file starts
    full: the failure mode of a corrupt file must not be a permanent stall."""
    try:
        data = json.loads(path.read_text())
        return float(data["tokens"]), float(data["updated"])
    except (OSError, ValueError, KeyError, TypeError):
        return budget.burst, time.time()


def _write_state(path: Path, tokens: float, updated: float) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps({"tokens": tokens, "updated": updated}))
    tmp.replace(path)


def acquire(tokens_path: str, timeout: float = DEFAULT_TIMEOUT_S) -> None:
    """Consume one request from the shared budget, waiting if necessary."""
    budget = Budget.from_env()
    directory = Path(tokens_path).expanduser().parent
    state_path = directory / STATE_FILENAME
    lock_file = directory / LOCK_FILENAME
    deadline = time.monotonic() + timeout

    while True:
        with file_lock(lock_file, timeout=timeout):
            now = time.time()
            tokens, updated = _read_state(state_path, budget)

            # A clock that jumped backwards must not mint tokens, and one that
            # jumped forwards must not hand over the whole bucket at once.
            elapsed = max(0.0, min(now - updated, budget.burst / budget.rate_per_sec))
            tokens = min(budget.burst, tokens + elapsed * budget.rate_per_sec)

            if tokens >= 1.0:
                _write_state(state_path, tokens - 1.0, now)
                return

            shortfall = (1.0 - tokens) / budget.rate_per_sec
            # Persist the refill so the wait is not recomputed from scratch.
            _write_state(state_path, tokens, now)

        # Sleep OUTSIDE the lock, or the other container cannot make progress
        # either and the budget is serialised into a queue.
        if time.monotonic() >= deadline:
            raise RateLimitTimeout(timeout)
        time.sleep(min(shortfall, _MAX_SLEEP_S, max(0.0, deadline - time.monotonic())))
