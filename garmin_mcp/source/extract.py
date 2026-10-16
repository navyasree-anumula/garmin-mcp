"""Readers for the four payload shapes that cannot be read the obvious way (§5).

Every function here exists because of a payload this project has actually seen.
None of them is defensive programming in the abstract:

- `at_path` -- VO2 max is at `mostRecentVO2Max.generic.vo2MaxValue`, three
  levels down a DTO that also carries `cycling: null` for a running-only
  account (5b, 2026-10-11).
- `select_primary_device` -- `training_status` keys its DTOs by **device id**:
  `mostRecentTrainingStatus.latestTrainingStatusData.3612870660`. The key is
  data, not a field name.
- `latest_by_timestamp` -- `training_readiness` returns `list(4)` for one day.
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


def latest_by_timestamp(snapshots: Any, key: str = "timestamp") -> Any:
    """The most recent of several snapshots for one day.

    `training_readiness` returns four for a single date (5b). Selected by the
    timestamp it carries, not by position: the list's order is not documented
    anywhere, and `[0]` happening to be newest is a property of one response.
    """
    if not isinstance(snapshots, list):
        return MISSING

    stamped = [
        snap
        for snap in snapshots
        if isinstance(snap, dict) and isinstance(snap.get(key), int | float)
    ]
    if not stamped:
        return MISSING
    return max(stamped, key=lambda snap: snap[key])


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
    "calendar_date": match_calendar_date,
}
