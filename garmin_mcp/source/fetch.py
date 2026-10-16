"""The seam (§6, v1.5). One request per distinct source, per tool call.

**This is the cache seam, shipped without the cache.** v1.5 split §6's
deferred cache in two: the seam lands with `garmin_training`, the store waits
for the second consumer. The reason is arithmetic -- one `garmin_training` call
is two or three requests against a burst of four, so the wait is already zero
and a store would save nothing -- and the duplication v1.4 wanted a cache for
turns out to live *inside* a single call, where `metrics.sources_for` removes it
before any request is made.

What remains for the store, when it comes: a key on the fetched payload plus the
map version, TTLs measured in 5c rather than invented, and an answer to the
question v1.5 opened -- a payload cache is health data at rest, on the volume
that holds the credential.

**Why this sits above `_garmin_access` rather than inside it.** That context
manager takes the cross-process lock first and the request budget second
(`client.py:52`, and the order is deliberate). A cache consulted *inside* it
would make a hit wait two seconds for a token it does not need, while holding
the lock that another container is queued on. A hit that is as slow as a miss is
not a cache.

**One doorway.** This module is the only caller of `client._garmin_access` on
the data path. `probe.py` is the documented exception: it deliberately probes
one candidate at a time and has no duplication to coalesce, so routing it
through here would add indirection and nothing else. `tests/test_fetch_seam.py`
enforces the rule and names that exception, because a discipline with no test is
a convention and conventions erode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from garminconnect import GarminConnectNotFoundError

from . import client as source
from . import verdicts
from .errors import RateLimited
from .metrics import CALL_DATE, CALL_NONE, SourceCall

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fetched:
    """What one source returned, or why it did not.

    `verdict` is about the *source*, not about a metric: `UNSUPPORTED` here
    means the endpoint 404'd for this account, which is the only reliable
    unsupported signal (§5). Whether an individual metric has a reading inside a
    successful payload is decided later, by its own path.
    """

    payload: Any = None
    verdict: str = verdicts.PRESENT
    detail: str = ""


def _args_for(call: SourceCall, date: str) -> tuple:
    """The arguments a source takes, resolved BEFORE the request is attempted.

    Deliberately outside the try/except in `fetch`: an unknown call shape is a
    map bug, and recording it as an `error` verdict would dress our own wiring
    mistake as a Garmin outcome. Raising here makes it a crash in a test rather
    than a plausible-looking "something went wrong" in a conversation.

    `get_race_predictions` takes zero arguments or all three and raises
    ValueError for anything between -- passing it a date is the exact bug that
    recorded an ERROR verdict for a month.
    """
    if call.call == CALL_NONE:
        return ()
    if call.call == CALL_DATE:
        return (date,)
    raise ValueError(f"unknown call shape {call.call!r} for {call.method}")


def fetch(
    api: Any,
    calls: tuple[SourceCall, ...],
    tokens_path: str,
    date: str,
) -> dict[SourceCall, Fetched]:
    """Make each distinct request once, and return what came back.

    A failure is recorded against its source rather than raised, so one dead
    endpoint cannot take down the four metrics that came from the two live ones.
    `RateLimited` is the exception, and it is re-raised: §6 forbids retrying into
    a throttle, and a swallowed 429 would reach the agent as "no data" -- the
    account blamed for Garmin's rate limit, which is precisely the mislabel §5's
    error/no_data split exists to prevent.
    """
    results: dict[SourceCall, Fetched] = {}

    for call in calls:
        if call in results:  # defensive: sources_for already de-duplicates
            continue
        args = _args_for(call, date)
        method = getattr(api, call.method)
        try:
            with source._garmin_access(tokens_path):
                payload = method(*args)
        except GarminConnectNotFoundError:
            logger.info("%s: 404, unsupported for this account", call.method)
            results[call] = Fetched(verdict=verdicts.UNSUPPORTED)
        except RateLimited:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded per source, not raised
            logger.info("%s failed: %s", call.method, type(exc).__name__)
            results[call] = Fetched(verdict=verdicts.ERROR, detail=type(exc).__name__)
        else:
            results[call] = Fetched(payload=payload)

    return results
