"""What was actually done: activities over a date window (§5, v1.8-A).

**This is the half of the weekly loop the server could not do.** Everything else
reads measurements or writes prescriptions; nothing until now could say what was
*run*, which means planned-versus-done was unknowable. `workoutId` on an
activity is the link, and step 0 measured it as present.

**The projection is an ALLOWLIST and that is the whole design.** Garmin's
activity row carries over a hundred keys, nine of which nobody would think to
deny:

- **Location** -- `startLatitude`, `startLongitude`, `locationName`, polylines.
  §9 names credentials and health data and is silent on location; this is the
  debt that silence has been carrying.
- **Identity** -- `ownerId`, `ownerDisplayName`, `ownerFullName` and three
  profile-image URLs, in *every row*. **`ownerDisplayName` is the GUID §10 calls
  load-bearing and deliberately does not record, because this repository is
  public.** Step 0 found this; §9 did not have a row for it.

A denylist would be a list nobody can finish, and it leaks the next field Garmin
adds. So this module names what may leave and drops the rest -- which also makes
it safe to have been written **before** a GPS run's row was ever seen: the
unmeasured risk is unknown extra fields, and that is precisely what an allowlist
refuses.

**An activity name is never logged.** It is user-written text and it can carry a
place -- "morning run in the park" -- so this module logs counts only.

**What is inferred here, and it is the unit.** Step 0 printed `distance: float`
and `duration: float`, which is a type and not a value, so "metres" and
"seconds" rest on Garmin's own metric account setting (§10) rather than on a
reading. The fields are named `distance_m` and `duration_s` and the note says
so, until one activity is read against Connect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from . import client as source
from .compose import format_pace_min_km
from .errors import GarminSourceError

logger = logging.getLogger(__name__)

#: The widest window this tool will read. A week is the planning unit and a
#: month is the review unit, so 31 days covers both -- and past that a read
#: stops being a question and becomes a scrape (§6). It is also the only control
#: available: `get_activities_by_date` paginates INSIDE one call, so the request
#: budget takes a single token for a loop whose length it cannot see.
MAX_WINDOW_DAYS = 31

#: What the library asks Garmin for per page. Used only to derive a request
#: count, because the pagination happens inside the library and cannot be
#: observed from here.
PAGE = 20

#: Named so a test can assert on them rather than trusting the prose above.
#: These are the keys measured present in a real activity row that must never
#: reach a response. The allowlist is what actually protects the output; this
#: exists so the protection can be *asserted* against the real field names.
NEVER_RETURNED = (
    "startLatitude",
    "startLongitude",
    "endLatitude",
    "endLongitude",
    "locationName",
    "geoPolylineDTO",
    "ownerId",
    "ownerDisplayName",
    "ownerFullName",
    "ownerProfileImageUrlSmall",
    "ownerProfileImageUrlMedium",
    "ownerProfileImageUrlLarge",
    "userRoles",
    "privacy",
    "deviceId",
    "timeZoneId",
)

NOTE = (
    "What was done, not what was planned -- `workout_id` is present when the "
    "activity came from a scheduled workout, which is what makes planned and "
    "done comparable. Distances are metres and durations seconds, INFERRED from "
    "the account's metric unit setting rather than read off a value; compare one "
    "activity against Connect to settle it. No location and no account identity "
    "leaves this tool: the projection names what may be returned and drops "
    "everything else, including fields Garmin may add later. No totals are "
    "computed -- what a week of this means needs how the legs feel and what "
    "hurts, which no endpoint holds (docs/SCOPE.md §1)."
)

EMPTY_NOTE = (
    "No activity in this window. That is an answer, not a failure: a week with "
    "nothing recorded reads exactly like this, and so does a week the watch was "
    "not worn."
)

REQUESTS_DERIVED = (
    "derived, not measured: the library pages 20 activities per request inside "
    "one call, so the count is inferred from the rows returned and is a lower "
    "bound. The local request budget sees one acquisition for the whole call"
)


class ActivityWindowRefused(GarminSourceError):
    """A window we will not read. Refused before any request is made."""


@dataclass(frozen=True)
class ExerciseSet:
    category: str
    sets: int | None
    reps: int | None
    max_weight_g: int | None
    duration_s: float | None


@dataclass(frozen=True)
class Activity:
    activity_id: int
    name: str
    start_local: str
    sport: str
    distance_m: float | None
    duration_s: float | None
    moving_duration_s: float | None
    avg_pace_min_km: str | None
    avg_hr_bpm: int | None
    max_hr_bpm: int | None
    aerobic_training_effect: float | None
    anaerobic_training_effect: float | None
    training_effect_label: str | None
    training_load: float | None
    hr_time_in_zone_s: dict[int, float]
    workout_id: int | None
    lap_count: int | None
    has_splits: bool | None
    exercise_sets: tuple[ExerciseSet, ...]


@dataclass(frozen=True)
class ActivityList:
    activities: tuple[Activity, ...]
    requests_made: int
    requests_basis: str
    note: str


def _number(value: Any) -> float | None:
    """A real number, never a bool. `isinstance(True, int)` is `True`."""
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, int | float) else None


def _whole(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _pace_for(distance_m: float | None, speed_m_s: Any) -> str | None:
    """Pace in min/km, or nothing at all.

    Driven off the data rather than off a list of sports: a strength circuit has
    no distance and no speed, and so does a rowing machine nobody has thought
    about yet. **Never `0:00`** -- that is a pace, and no pace is the truth.

    No band is applied. `parse_pace_min_km`'s 150-720 s/km refuses a typo in a
    prescription; a walk is slower than that and happened anyway.
    """
    speed = _number(speed_m_s)
    if not distance_m or distance_m <= 0 or speed is None or speed <= 0:
        return None
    return format_pace_min_km(1000.0 / speed)


def _zones_for(row: dict[str, Any]) -> dict[int, float]:
    """Seconds in each HR zone that Garmin actually reported.

    **Never padded.** A zero means "no time in that zone", which is a claim; an
    absent key means Garmin did not say, and the two are different answers to
    "was the easy run easy".
    """
    out: dict[int, float] = {}
    for zone in range(1, 6):
        seconds = _number(row.get(f"hrTimeInZone_{zone}"))
        if seconds is not None:
            out[zone] = seconds
    return out


def _sets_for(row: dict[str, Any]) -> tuple[ExerciseSet, ...]:
    """What a strength session actually did, as Garmin summarises it.

    Garmin reports reps and weight even though v1.6 prescribes a circuit by
    time, so reading them costs nothing and is the cheap half of the
    reps-and-weight question that stays deferred. `maxWeight` is in GRAMS
    upstream (`workout.py:93` stores `weightValue` the same way), so the field
    says `_g` rather than quietly dividing by a thousand.
    """
    raw = row.get("summarizedExerciseSets")
    if not isinstance(raw, list):
        return ()

    return tuple(
        ExerciseSet(
            category=str(entry.get("category") or "unknown"),
            sets=_whole(entry.get("sets")),
            reps=_whole(entry.get("reps")),
            max_weight_g=_whole(entry.get("maxWeight")),
            duration_s=_number(entry.get("duration")),
        )
        for entry in raw
        if isinstance(entry, dict)
    )


def _project(row: dict[str, Any]) -> Activity | None:
    """One activity, reduced to what a training decision needs.

    Returns `None` for a row with no usable id: an activity that cannot be named
    cannot be asked about later, and inventing an id would be worse.
    """
    activity_id = _whole(row.get("activityId"))
    if activity_id is None:
        return None

    kind = row.get("activityType")
    sport = kind.get("typeKey") if isinstance(kind, dict) else None
    distance_m = _number(row.get("distance"))

    # A `workoutId` of 0 is NOT an id. Reading one as an id would link this
    # session to a workout that does not exist -- the §16.2a defect in a new
    # place, where a fallback was safe only while every key meant the same
    # thing.
    workout_id = _whole(row.get("workoutId")) or None

    return Activity(
        activity_id=activity_id,
        name=str(row.get("activityName") or "(unnamed)"),
        start_local=str(row.get("startTimeLocal") or ""),
        sport=str(sport or "unknown"),
        distance_m=distance_m,
        duration_s=_number(row.get("duration")),
        moving_duration_s=_number(row.get("movingDuration")),
        avg_pace_min_km=_pace_for(distance_m, row.get("averageSpeed")),
        avg_hr_bpm=_whole(row.get("averageHR")),
        max_hr_bpm=_whole(row.get("maxHR")),
        aerobic_training_effect=_number(row.get("aerobicTrainingEffect")),
        anaerobic_training_effect=_number(row.get("anaerobicTrainingEffect")),
        training_effect_label=(
            str(row["trainingEffectLabel"])
            if isinstance(row.get("trainingEffectLabel"), str)
            else None
        ),
        training_load=_number(row.get("activityTrainingLoad")),
        hr_time_in_zone_s=_zones_for(row),
        workout_id=workout_id,
        lap_count=_whole(row.get("lapCount")),
        has_splits=row.get("hasSplits") if isinstance(row.get("hasSplits"), bool) else None,
        exercise_sets=_sets_for(row),
    )


def _check_window(start_date: str, end_date: str) -> int:
    """Refuse a window before spending a request on it.

    The library validates the date FORMAT and nothing else -- not the order, not
    the span -- so backwards dates would reach Garmin and a decade-wide window
    would page through years of history inside one call that the request budget
    counts as one.
    """
    from datetime import date

    try:
        first = date.fromisoformat(start_date)
        last = date.fromisoformat(end_date)
    except ValueError as exc:
        raise ActivityWindowRefused(
            f"{start_date!r} to {end_date!r} is not a pair of YYYY-MM-DD dates. "
            "Nothing was requested."
        ) from exc

    if last < first:
        raise ActivityWindowRefused(
            f"the window runs backwards: {start_date} is after {end_date}. "
            "Nothing was requested."
        )

    span = (last - first).days + 1
    if span > MAX_WINDOW_DAYS:
        raise ActivityWindowRefused(
            f"{start_date} to {end_date} is {span} days, and this tool reads at "
            f"most {MAX_WINDOW_DAYS}. Ask for a narrower window: a wide one "
            "pages through history inside a single call, which the local request "
            "budget cannot see and Garmin reads as a scrape. Nothing was "
            "requested."
        )
    return span


def read_window(
    start_date: str,
    end_date: str,
    sport: str | None = None,
    tokens_path: str | None = None,
) -> ActivityList:
    """Activities between two dates, projected to what training needs.

    One `_garmin_access`, and the library pages 20 at a time inside it -- so the
    request count is derived rather than observed, and says so.
    """
    _check_window(start_date, end_date)
    path = tokens_path or source.tokenstore_path()
    api = source._connect()

    with source._garmin_access(path):
        payload = api.get_activities_by_date(start_date, end_date, sport)

    rows = [row for row in payload or [] if isinstance(row, dict)]
    projected = tuple(
        activity for activity in (_project(row) for row in rows) if activity is not None
    )

    # Count only. An activity NAME is user text that can carry a place.
    logger.info(
        "activities %s..%s: %d row(s), %d projected",
        start_date,
        end_date,
        len(rows),
        len(projected),
    )

    return ActivityList(
        activities=projected,
        requests_made=len(rows) // PAGE + 1,
        requests_basis=REQUESTS_DERIVED,
        note=NOTE if projected else EMPTY_NOTE,
    )
