"""The account's heart-rate zones: configuration, not a measurement (§5, v1.8).

**Why this is not a metric-map row.** Every row in `metrics.py` is one value,
one unit, one source and one `measured_on`. A zone table is five rows per sport
profile and **has no date at all** -- it is how the watch is configured, not
something it recorded on a day. Bending `measured_on` to carry a profile would
weaken the single field that makes every other reading auditable, so the zones
get their own small reader and the map keeps its invariant.

**Why this takes `_garmin_access` directly instead of going through `fetch.py`.**
That seam exists to coalesce *metric* reads keyed by a `SourceCall`, so that two
metrics sharing a payload cost one request. This payload belongs to no metric
and takes no date, so there is nothing to coalesce it with. `write.py` reached
past the same seam for the same kind of reason, and
`tests/test_fetch_seam.py`'s allowlist had to gain a line on purpose -- which is
what that list is for.

**What is measured here and what is inferred.** `probe --explain
heart_rate_zones` on 2026-11-04 recorded the key names and types: five
`zoneNFloor` ints, `maxHeartRateUsed`, `lactateThresholdHeartRateUsed`,
`restingHeartRateUsed`, `trainingMethod`, `sport`, `restingHrAutoUpdateUsed`
and `changeState`. **The zones are FLOORS**, so every ceiling is arithmetic over
Garmin's own numbers rather than a field: zone N ends where zone N+1 begins, and
**zone 5's ceiling is inferred to be `maxHeartRateUsed`** -- the one inference in
this module, labelled in the response itself and not only in a document, because
v1.9 converts a zone to bpm against exactly these bounds.

`trainingMethod` is returned although nothing in v1.8 reads it. That is
deliberate: the same zone number means different bpm under percentage-of-max
than under percentage-of-threshold, so v1.9's converter is wrong without it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from . import client as source

logger = logging.getLogger(__name__)

#: Garmin returns the boundaries as floors, one per zone, in a flat payload.
FLOOR_KEYS = ("zone1Floor", "zone2Floor", "zone3Floor", "zone4Floor", "zone5Floor")

#: What a ceiling is derived from, carried in the response so a reader never has
#: to guess which of the two it got.
CEILING_NEXT_FLOOR = "the next zone's floor, measured"
CEILING_MAX_HR = "maxHeartRateUsed -- INFERRED, no field gives zone 5's ceiling"
CEILING_UNKNOWN = "unknown -- the next floor is missing or not above this one"


@dataclass(frozen=True)
class Zone:
    """One zone's bounds. `ceiling_bpm` may be None, and then it says why."""

    zone: int
    floor_bpm: int
    ceiling_bpm: int | None
    ceiling_basis: str


@dataclass(frozen=True)
class ZoneProfile:
    """One sport profile's zones, as Garmin has them configured."""

    sport: str
    training_method: str
    max_hr_bpm: int | None
    threshold_hr_bpm: int | None
    resting_hr_used_bpm: int | None
    zones: tuple[Zone, ...]
    change_state: str


@dataclass(frozen=True)
class ZoneReading:
    profiles: tuple[ZoneProfile, ...]
    requests_made: int
    note: str


def _int_or_none(value: Any) -> int | None:
    """An int, and not a bool. A `True` read as 1 bpm would be a silent lie."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _zones_from(profile: dict[str, Any], max_hr: int | None) -> tuple[Zone, ...]:
    """Pair the floors into ranges, refusing a pair it cannot justify.

    A missing floor, or one that does not ascend, leaves the ceiling UNSET with
    its reason attached rather than producing a range that reads as measured. A
    fabricated ceiling here becomes a wrong heart-rate target in v1.9, which is
    a wrong number on a watch -- and a missing ceiling is an answer, while a
    wrong one is not.
    """
    floors = [_int_or_none(profile.get(key)) for key in FLOOR_KEYS]
    out: list[Zone] = []

    for index, floor in enumerate(floors):
        if floor is None:
            continue

        number = index + 1
        if number < len(floors):
            nxt = floors[number]
            if nxt is not None and nxt > floor:
                out.append(Zone(number, floor, nxt, CEILING_NEXT_FLOOR))
            else:
                out.append(Zone(number, floor, None, CEILING_UNKNOWN))
            continue

        # Zone 5: the top. No field carries its ceiling.
        if max_hr is not None and max_hr > floor:
            out.append(Zone(number, floor, max_hr, CEILING_MAX_HR))
        else:
            out.append(Zone(number, floor, None, CEILING_UNKNOWN))

    return tuple(out)


def _profile_from(profile: dict[str, Any]) -> ZoneProfile:
    max_hr = _int_or_none(profile.get("maxHeartRateUsed"))
    return ZoneProfile(
        sport=str(profile.get("sport") or "unknown"),
        training_method=str(profile.get("trainingMethod") or "unknown"),
        max_hr_bpm=max_hr,
        threshold_hr_bpm=_int_or_none(profile.get("lactateThresholdHeartRateUsed")),
        resting_hr_used_bpm=_int_or_none(profile.get("restingHeartRateUsed")),
        zones=_zones_from(profile, max_hr),
        change_state=str(profile.get("changeState") or "unknown"),
    )


EMPTY_NOTE = (
    "Garmin returned no zone profile for this account. That is reported rather "
    "than treated as zero zones: nothing can be converted against it, and a "
    "heart-rate target composed from an empty table would be a guess."
)

NOTE = (
    "Configuration as Garmin holds it, not a measurement of a day -- these "
    "numbers have no date. Zone bounds are FLOORS in the payload, so each "
    "ceiling is derived and every zone says which way: the next zone's floor, "
    "or (for zone 5 only) the inferred maxHeartRateUsed. `max_hr_bpm`, "
    "`threshold_hr_bpm` and `resting_hr_used_bpm` are health numbers in a "
    "payload that reads as settings. Which zone to run in is a judgement that "
    "needs how the legs feel and what the session is for, which no endpoint "
    "holds (docs/SCOPE.md §1)."
)


def read_zones(tokens_path: str | None = None) -> ZoneReading:
    """Every configured heart-rate zone profile, in one request.

    `get_heart_rate_zones()` takes no arguments and answers for all sport
    profiles the account has -- though on 2026-11-04 this account returned
    exactly one, so **nothing here assumes the profile it wants is first, or
    present at all.** The caller reads `sport` and decides; v1.9's converter
    refuses rather than guessing.
    """
    path = tokens_path or source.tokenstore_path()
    api = source._connect()

    with source._garmin_access(path):
        payload = api.get_heart_rate_zones()

    if isinstance(payload, dict):
        # Defensive: a single profile returned unwrapped. Never seen; cheaper to
        # handle than to debug from a conversation.
        payload = [payload]

    rows = [row for row in payload or [] if isinstance(row, dict)]
    logger.info("heart rate zones: %d profile(s)", len(rows))

    return ZoneReading(
        profiles=tuple(_profile_from(row) for row in rows),
        requests_made=1,
        note=NOTE if rows else EMPTY_NOTE,
    )
