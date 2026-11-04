"""Readers for the four payload shapes that cannot be read the obvious way (§5).

Every function here exists because of a payload this project has actually seen.
None of them is defensive programming in the abstract:

- `at_path` -- VO2 max is at `mostRecentVO2Max.generic.vo2MaxValue`, three
  levels down a DTO that also carries `cycling: null` for a running-only
  account (5b, 2026-10-11).
- `select_primary_device` -- `training_status` keys its DTOs by **device id**:
  `mostRecentTrainingStatus.latestTrainingStatusData.3612870660`. The key is
  data, not a field name.
- `latest_by_timestamp` -- `training_readiness` returned `list(4)` for one day in 5b
  and `list(1)` on 2026-11-04, which is why selection is by timestamp and not by
  position. Its timestamp is a **`str`**, measured 2026-11-04.
- `match_calendar_date` -- resting HR's `DATE` argument returns a statistics
  window, so the reading carries its own `calendarDate` and must be matched
  against the date that was asked for.

**Absence is a return value, not an exception.** Each function returns
`MISSING` when it cannot find a reading, and the caller maps that onto a
verdict. Exceptions are reserved for *our* failures (§5: `error` means our call
failed, never Garmin's absence of data), and a missing key is not one.

**`None` counts as missing.** This is the `spo2` lesson from 2026-10-09: a
payload with `averageSpO2: null` and eight populated window timestamps read as
`has_data` under a counter that treated a present key as a reading. A key whose
value is `null` is not a measurement.
"""

from __future__ import annotations

from typing import Any


class _Missing:
    """Sentinel. A class rather than `None` because `None` is exactly the value
    these functions have to be able to distinguish themselves from."""

    _instance: "_Missing | None" = None

    def __new__(cls) -> "_Missing":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()


def at_path(payload: Any, path: str) -> Any:
    """Walk a dotted path. `MISSING` for anything that is not a reading.

    An empty `path` returns the payload itself, which is how a map row names a
    source whose whole response is the value.
    """
    if not path:
        return payload if payload is not None else MISSING

    node: Any = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return MISSING
        node = node[part]
    return MISSING if node is None else node


def select_primary_device(dto_map: Any) -> Any:
    """The DTO for the watch actually being trained with.

    Selects on `primaryTrainingDevice: bool`, which both DTOs carry. Never
    index 0 and never a hardcoded device id -- the id works on this account
    only, and breaks on this account the day the watch is replaced.

    **Zero matches and two matches both return `MISSING`, deliberately.** With
    no flag set, picking the only entry would answer with a secondary device's
    training status and look right; with two set, picking either is the
    hardcoding mistake wearing a different hat. Both are payload shapes this
    project has not seen, and the honest answer to an unseen shape is "no
    reading", which surfaces as `no_data` rather than as a confident wrong
    number.
    """
    if not isinstance(dto_map, dict):
        return MISSING

    primary = [
        dto
        for dto in dto_map.values()
        if isinstance(dto, dict) and dto.get("primaryTrainingDevice") is True
    ]
    return primary[0] if len(primary) == 1 else MISSING


#: The `inputContext` value Garmin tags the post-wake snapshot with. Upstream's
#: own morning helper filters on this string; the string is the only part of
#: that helper this module borrows (see `morning_snapshot`).
WAKEUP_CONTEXT = "AFTER_WAKEUP_RESET"


def _is_number(value: Any) -> bool:
    """A number, and **not** a bool.

    `isinstance(True, int)` is `True` in Python, so a timestamp of `True` would
    pass a naive numeric test and then compare as 1 -- older than every real
    timestamp, which would make it win a `min` and lose a `max` silently.
    """
    return isinstance(value, int | float) and not isinstance(value, bool)


def latest_by_timestamp(snapshots: Any, key: str = "timestamp") -> Any:
    """The most recent of several snapshots for one day.

    Selected by the timestamp it carries, not by position: the list's order is
    not documented anywhere, and `[0]` happening to be newest is a property of
    one response.

    **The type of that timestamp is a measurement, and this function had it
    wrong for as long as it existed.** It required `int | float`; `probe
    --explain training_readiness` on 2026-11-04 shows `timestamp: str`. So every
    snapshot was filtered out, this returned `MISSING`, and `readiness` and
    `recovery_time` reported `no_data` against a payload that held the data --
    while five tests passed, because every fixture invented a numeric timestamp
    from the same assumption as the code. **An inferred type is exactly as
    unsourced as an inferred field name**, and `verified=False` was pointing at
    the wrong half of the row.

    Both kinds are now accepted, **but never mixed**. A `max()` across `str` and
    `int` is a `TypeError`, and coercing to make it work would pick an arbitrary
    winner from a list whose order means nothing. "No data" is the honest answer
    to a payload we cannot order.

    ISO-8601 strings compare correctly under `max` only while they share one
    format. That is why the readiness rows read `timestamp` (UTC) and never
    `timestampLocal`: one basis, one format, and a mix refuses rather than
    guesses.
    """
    if not isinstance(snapshots, list):
        return MISSING

    stamped = [
        snap
        for snap in snapshots
        if isinstance(snap, dict) and snap.get(key) is not None
    ]
    if not stamped:
        return MISSING

    stamps = [snap[key] for snap in stamped]
    if all(_is_number(stamp) for stamp in stamps):
        return max(stamped, key=lambda snap: snap[key])
    if all(isinstance(stamp, str) for stamp in stamps):
        return max(stamped, key=lambda snap: snap[key])
    return MISSING


def morning_snapshot(snapshots: Any, key: str = "inputContext") -> Any:
    """The snapshot Garmin took after waking, or nothing.

    SCOPE v1.8 decision 4: the morning reading is the source that wins, because
    a check-in wants one snapshot taken at a known moment rather than whichever
    was most recent when the question was asked.

    **Deliberately not `api.get_morning_training_readiness`.** That helper
    filters on this same string and then, when nothing matches, `return data[0]`
    (`garminconnect/__init__.py:2102`, admitted in its own docstring). That is a
    silent fallback, which §5 forbids -- *fallback is explicit or absent* -- and
    it is a fallback **by position**, which is the one selection rule this
    module exists to refuse. It also buys nothing: that helper calls
    `get_training_readiness` internally, so reading the list ourselves is the
    same single request.

    So a day with snapshots but no morning one is `MISSING`, which surfaces as
    `no_data`. That is indistinguishable from a day with no snapshots at all,
    and the two want opposite responses -- which is why `readiness_latest` ships
    beside `readiness` out of the same payload, as the named alternative rather
    than a quiet substitution.
    """
    if not isinstance(snapshots, list):
        return MISSING

    morning = [
        snap
        for snap in snapshots
        if isinstance(snap, dict) and snap.get(key) == WAKEUP_CONTEXT
    ]
    if not morning:
        return MISSING

    # More than one is possible -- a device reset, or the watch re-worn after a
    # nap -- so the latest of the matches wins, by the rule above and not by
    # position.
    return latest_by_timestamp(morning)


def match_calendar_date(rows: Any, date: str, key: str = "calendarDate") -> Any:
    """The row whose own date equals the date that was asked for.

    Resting HR's `DATE` argument returns a *statistics window*, so a response
    exists for a date that has no reading. **The nearest value is never
    returned**: "no data for this date" is an answer a coach can act on, and a
    neighbouring day's resting heart rate presented as today's is not.
    """
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return MISSING

    for row in rows:
        if isinstance(row, dict) and row.get(key) == date:
            return row
    return MISSING


#: Decoder names a map row may ask for. A row with no decoder reads straight
#: through `at_path`.
DECODERS = {
    "primary_device": select_primary_device,
    "latest_timestamp": latest_by_timestamp,
    "morning_snapshot": morning_snapshot,
    "calendar_date": match_calendar_date,
}
