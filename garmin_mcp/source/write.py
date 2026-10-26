"""Writing a workout to Garmin: resolve, write, schedule, report (§5, v1.5).

**The rule this module is built around: once the workout exists, nothing raises.**
`upload_workout` mints a template on Garmin and `schedule_workout` is a second
request. An exception between them loses the `workoutId`, leaving a template on
the account that appears on no calendar -- and next week's session reads the
*calendar*, so it will never see it. Every failure after the write therefore
comes back as an `Outcome` describing how far it got, including a 429. §5 calls
this "a partial write reports what it got to"; here it is the difference between
a mess you can clean up and a mess you cannot find.

**Why this takes `_garmin_access` directly instead of going through `fetch.py`.**
That seam exists to coalesce *reads* -- two metrics sharing a payload cost one
request. Coalescing a write would mean silently dropping one, which has no
upside at all. `tests/test_fetch_seam.py` keeps an explicit allowlist of modules
permitted at the doorway, and this module had to be added to it on purpose.

**What is unverified here, and it is most of the response handling.**
`upload_workout` and `schedule_workout` have never been called by this project,
so the keys their responses carry are unknown. Known from the library's own code
rather than from a payload: `get_workouts` items carry `workoutName` and
`workoutId` (`garminconnect/__init__.py:3403`). Everything else is read through
`_first_id`, which tries candidates and reports a miss instead of guessing --
because a guess that misses would throw away an id that already exists.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Any

from . import client as source
from .errors import GarminSourceError, RateLimited

logger = logging.getLogger(__name__)

#: One page of `get_workouts`. The library's own default, kept so a page that
#: comes back short means "the end" rather than "a different limit".
PAGE = 100

#: How many pages to walk before refusing. 1000 workouts is far beyond one
#: athlete's library, so hitting this means something is wrong -- and matching a
#: name against a truncated list is how a re-prescription silently becomes a
#: duplicate instead of an update.
MAX_PAGES = 10

#: Where a workout id might live in a response nobody has read yet. Order is
#: preference, not likelihood.
WORKOUT_ID_KEYS = ("workoutId", "id")

#: Same, for the schedule. §5: undo needs the scheduled id and NOT the workout
#: id -- `unschedule_workout` takes the former -- so a miss here costs the only
#: rollback path and has to be said out loud.
SCHEDULE_ID_KEYS = ("workoutScheduleId", "scheduledWorkoutId", "id")

#: Containers a calendar month might arrive in. The distinction that matters is
#: between "this key exists and is empty" (nothing scheduled, a normal month)
#: and "none of these keys exist" (we are not reading the shape we think we
#: are) -- those have opposite conclusions and look identical otherwise.
CALENDAR_KEYS = ("calendarItems", "items", "workoutScheduleList")

UPLOADED = "uploaded"
UPDATED = "updated"

SCHEDULED_NEW = "new"
SCHEDULED_ALREADY = "already"
SCHEDULED_FAILED = "failed"


#: The gate. Default closed, so an image that reaches a laptop cannot write to
#: a Garmin account until somebody edits the client config and restarts -- which
#: a new build needs anyway, so the gate costs nothing it was not already
#: costing. SCOPE 14's 7.b asked for "are writes enabled right now" to be a
#: question the agent can ask; this is the answer, pending `tools.json`.
WRITES_ENV = "GARMIN_MCP_ENABLE_WRITES"

#: Anything else is off, including "yes", "on" and "TRUE " with a space. A gate
#: that accepts a near-miss is a gate whose state nobody can predict.
WRITES_ON = ("1", "true")


def writes_enabled() -> bool:
    """Read per call, never cached at import.

    Two consumers -- the refusal in the create tool and the field on
    `garmin_auth_status` -- and they must agree. A module-level constant read at
    import time would let the status report a value the refusal no longer uses,
    which is worse than no field at all: the agent would be told writes are on
    and then be refused, or told they are off and write anyway.
    """
    return os.environ.get(WRITES_ENV, "").strip().lower() in WRITES_ON


class WritesDisabled(GarminSourceError):
    """Writes are off. Raised before anything reaches Garmin."""

    def __init__(self) -> None:
        super().__init__(
            f"Writes are not enabled on this server, so nothing was sent to "
            f"Garmin. Set {WRITES_ENV}=1 in the Claude Desktop config's `env` "
            f"block and restart Claude Desktop (the client caches tool schemas "
            f"at session start). `garmin_auth_status` reports whether writes are "
            f"enabled, so it can be checked before composing anything."
        )


class WriteRefused(GarminSourceError):
    """Refused **before** anything reached Garmin.

    Separate from an `Outcome` carrying a failure, and the separation is the
    point: this exception means the account is untouched, so raising it loses
    nothing. After the write, failures are reported instead.
    """


@dataclass(frozen=True)
class Resolution:
    """Upload a new template, or replace one that already has this name."""

    action: str
    workout_id: int | None = None


@dataclass
class Outcome:
    """How far a write got, and everything needed to finish or undo it."""

    action: str
    workout_id: int | None
    scheduled: str
    scheduled_workout_id: int | None = None
    date: str = ""
    requests_made: int = 0
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _first_id(payload: Any, keys: tuple[str, ...]) -> int | None:
    """An id from a response shape nobody has measured yet.

    Looks at the top level and one level down -- Garmin wraps some responses in
    a single-key envelope and this costs nothing to tolerate. Returns None
    rather than raising on a miss: by the time this is called the write has
    already happened, and an exception would destroy the id's only copy.

    Non-integer values are skipped rather than coerced. `schedule_workout` runs
    its argument through the library's `_validate_positive_integer`, so a string
    that happens to be numeric would pass here and fail there, one layer further
    from the thing that could explain it.
    """
    if not isinstance(payload, dict):
        return None

    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):  # bool is an int; it is not an id
            continue
        if isinstance(value, int):
            return value

    for value in payload.values():
        if isinstance(value, dict):
            found = _first_id(value, keys)
            if found is not None:
                return found
    return None


def list_workouts(api: Any, tokens_path: str) -> tuple[list[dict[str, Any]], int]:
    """Every workout on the account, and how many requests that cost.

    Pages rather than trusting one call. `get_workouts` defaults to 100 and
    silently returns the first page, so a name match against it would miss a
    workout at position 101 -- and a missed match uploads a duplicate rather
    than updating in place, which is the exact failure the name convention
    exists to prevent.

    Refuses at `MAX_PAGES` instead of matching against what it has. A refusal
    the human can act on beats a duplicate they discover on their watch.
    """
    found: list[dict[str, Any]] = []
    requests = 0

    for page in range(MAX_PAGES):
        with source._garmin_access(tokens_path):
            batch = api.get_workouts(start=page * PAGE, limit=PAGE)
        requests += 1

        if not isinstance(batch, list):
            raise WriteRefused(
                f"get_workouts returned {type(batch).__name__}, not a list. "
                "Nothing was written."
            )
        found.extend(batch)
        if len(batch) < PAGE:
            return found, requests

    raise WriteRefused(
        f"this account has more than {MAX_PAGES * PAGE} workouts, so a name "
        "cannot be matched against a complete list. Nothing was written: "
        "matching against a partial list would upload a duplicate instead of "
        "updating in place."
    )


def resolve(name: str, existing: list[dict[str, Any]]) -> Resolution:
    """Name match means update in place; no match means upload (§5).

    `update_workout` replaces the whole workout via PUT and **keeps the id**, so
    every calendar entry pointing at it stays valid. That is why a
    re-prescription resolves to an update rather than a second upload: two
    templates called "week 3 Thursday threshold" would leave the human unable to
    tell which one the watch is going to run.

    Two or more matches is refused rather than resolved. Picking one would be
    arbitrary, and the arbitrariness would be invisible -- the response would
    say "updated" and look entirely correct.
    """
    wanted = name.strip()
    if not wanted:
        raise WriteRefused("a workout needs a name to match on.")

    matches = [
        item
        for item in existing
        if isinstance(item, dict)
        and str(item.get("workoutName", "")).strip() == wanted
    ]

    if not matches:
        return Resolution(UPLOADED)

    if len(matches) > 1:
        raise WriteRefused(
            f"{len(matches)} workouts are already called {wanted!r}. Nothing was "
            "written: updating one of them would pick arbitrarily, and the "
            "response would look correct either way. Rename or delete the "
            "duplicates first."
        )

    workout_id = _first_id(matches[0], WORKOUT_ID_KEYS)
    if workout_id is None:
        raise WriteRefused(
            f"the existing workout called {wanted!r} carries no readable id, so "
            "it cannot be updated in place. Nothing was written."
        )
    return Resolution(UPDATED, workout_id)


def _scheduled_ids_on(
    payload: Any, workout_id: int, day: str
) -> tuple[int | None, bool]:
    """Whether this workout already sits on this date, and its schedule id.

    Returns `(schedule_id, shape_understood)`. The second value is what matters:
    an empty month and an unrecognised response look identical, and they have
    opposite conclusions -- one means "go ahead and schedule", the other means
    "we are not reading what we think we are".
    """
    if not isinstance(payload, dict):
        return None, False

    items = None
    for key in CALENDAR_KEYS:
        if key in payload:
            items = payload[key]
            break
    if not isinstance(items, list):
        return None, False

    for item in items:
        if not isinstance(item, dict):
            continue
        if _first_id(item, WORKOUT_ID_KEYS) != workout_id:
            continue
        on = str(item.get("date") or item.get("calendarDate") or "")
        if on.startswith(day):
            return _first_id(item, SCHEDULE_ID_KEYS), True
    return None, True


def create_and_schedule(
    api: Any,
    tokens_path: str,
    structure: dict[str, Any],
    day: str,
) -> Outcome:
    """Write the workout and put it on the calendar, as one action (§5).

    Order, and why: resolve first (so a re-prescription updates rather than
    duplicates), then -- only on the update path -- read the target month, then
    write, then schedule. The calendar read happens **before** the write because
    it is the last point at which refusing is free; after the write, a refusal
    is not available any more, only a report.

    A fresh upload skips the calendar read entirely. A workout that does not
    exist yet cannot already be scheduled, and the request would buy nothing.
    """
    name = str(structure.get("workoutName", "")).strip()
    try:
        date_type.fromisoformat(day)
    except ValueError as exc:
        raise WriteRefused(
            f"{day!r} is not a YYYY-MM-DD date. Nothing was written."
        ) from exc

    existing, requests = list_workouts(api, tokens_path)
    plan = resolve(name, existing)

    already_id: int | None = None
    if plan.action == UPDATED:
        year, month = int(day[:4]), int(day[5:7])
        try:
            with source._garmin_access(tokens_path):
                calendar = api.get_scheduled_workouts(year, month)
        except RateLimited:
            raise
        except Exception as exc:  # noqa: BLE001 - refusing is still free here
            raise WriteRefused(
                f"could not read the calendar for {year}-{month:02d} "
                f"({type(exc).__name__}), so whether this workout is already on "
                f"{day} is unknown. Nothing was written: scheduling blind would "
                "risk the same session twice on one day."
            ) from exc
        requests += 1

        already_id, understood = _scheduled_ids_on(calendar, plan.workout_id, day)
        if not understood:
            raise WriteRefused(
                f"the calendar response for {year}-{month:02d} is not a shape "
                "this server recognises, so an existing schedule cannot be "
                "ruled out. Nothing was written. A new name would take the "
                "upload path, which needs no calendar read."
            )

    # ---- past this line the account can change, so nothing may raise --------
    if plan.action == UPLOADED:
        with source._garmin_access(tokens_path):
            written = api.upload_workout(structure)
    else:
        with source._garmin_access(tokens_path):
            written = api.update_workout(plan.workout_id, structure)
    requests += 1

    workout_id = _first_id(written, WORKOUT_ID_KEYS) or plan.workout_id
    outcome = Outcome(
        action=plan.action,
        workout_id=workout_id,
        scheduled=SCHEDULED_FAILED,
        date=day,
        requests_made=requests,
    )

    if workout_id is None:
        # The template exists and we cannot name it. Everything else is now
        # impossible, so say so rather than attempting a call that will fail.
        outcome.raw = {"write_response": written}
        outcome.warnings.append(
            "The workout was written but its id is not in a key this server "
            "recognises, so it could not be scheduled and cannot be undone from "
            "here. The raw response is included. Find it by name in Garmin "
            "Connect."
        )
        return outcome

    if already_id is not None:
        outcome.scheduled = SCHEDULED_ALREADY
        outcome.scheduled_workout_id = already_id
        return outcome

    try:
        with source._garmin_access(tokens_path):
            booked = api.schedule_workout(workout_id, day)
    except Exception as exc:  # noqa: BLE001 - the workout exists; report it
        outcome.warnings.append(
            f"The workout was {plan.action} as id {workout_id}, then scheduling "
            f"it for {day} failed ({type(exc).__name__}). **It exists on Garmin "
            "and is on no calendar**, so nothing reading the calendar will see "
            "it -- including this server. Schedule or delete it by id."
        )
        return outcome
    requests += 1
    outcome.requests_made = requests

    outcome.scheduled = SCHEDULED_NEW
    outcome.scheduled_workout_id = _first_id(booked, SCHEDULE_ID_KEYS)
    if outcome.scheduled_workout_id is None:
        outcome.raw = {"schedule_response": booked}
        outcome.warnings.append(
            "Scheduled, but the schedule id is not in a key this server "
            "recognises. Undo needs that id and not the workout id, so removing "
            "this from the calendar has to be done in Garmin Connect. The raw "
            "response is included."
        )
    return outcome
