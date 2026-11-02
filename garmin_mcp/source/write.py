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
#: `workoutId` is CONFIRMED for `upload_workout`, measured 2026-10-31 by
#: `id_keys` on the `Runner strength 30` create. The fallbacks stay, and are not
#: dead weight: this tuple is also read against **calendar items** in
#: `_scheduled_ids_on` and against the **workout list** in the name resolver,
#: neither of which has had its key names measured. Narrowing it to the one
#: confirmed key would be reading one endpoint's evidence as three endpoints'.
WORKOUT_ID_KEYS = ("workoutId", "id")

#: **A calendar item is a different shape, measured 2026-11-02 on the first live
#: undo, and the difference is dangerous.** That item carried `workoutId` for the
#: template and **`id` for the SCHEDULE** -- so `WORKOUT_ID_KEYS`' `id` fallback
#: would read a schedule id as a workout id. On this account every item carries
#: `workoutId`, so the fallback never fired; an item that omits it (a scheduled
#: course, a race, an item type nobody has seen) would have matched on a number
#: that means something else, and the undo would have removed **a different
#: session** while reporting success. No `id` fallback here, on purpose: a
#: missing `workoutId` must read as "no match" and not as a guess.
CALENDAR_WORKOUT_ID_KEYS = ("workoutId",)

#: Same, for the schedule. §5: undo needs the scheduled id and NOT the workout
#: id -- `unschedule_workout` takes the former -- so a miss here costs the only
#: rollback path and has to be said out loud.
#:
#: `workoutScheduleId` is CONFIRMED for `schedule_workout`, same measurement.
#:
#: **And `id` is CONFIRMED for a calendar ITEM, measured 2026-11-02** -- the
#: third candidate answered, and the undo it drove removed the right entry, so
#: the candidate list earned its keep on the one shape nobody had read. The two
#: endpoints genuinely disagree about the key name for the same number, which is
#: why this stayed a list of candidates rather than becoming one constant.
SCHEDULE_ID_KEYS = ("workoutScheduleId", "scheduledWorkoutId", "id")

#: Containers a calendar month might arrive in. The distinction that matters is
#: between "this key exists and is empty" (nothing scheduled, a normal month)
#: and "none of these keys exist" (we are not reading the shape we think we
#: are) -- those have opposite conclusions and look identical otherwise.
CALENDAR_KEYS = ("calendarItems", "items", "workoutScheduleList")

#: How a calendar item might spell its date. Both are tried and **which one
#: answered is recorded**, because the first live undo settled the ids and left
#: this unmeasured. Prefix-matched against `YYYY-MM-DD` rather than compared,
#: since Garmin's dates carry a time on some endpoints -- and that prefix match
#: is also what makes a month view containing neighbouring days harmless.
CALENDAR_DATE_KEYS = ("date", "calendarDate")

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
    #: Which response key each id was read from, e.g. {"workout_id":
    #: "workoutId"}. Recorded on SUCCESS, which is the case that was previously
    #: silent -- see `_first_id_with_key`.
    id_keys: dict[str, str] = field(default_factory=dict)


def _first_id_with_key(
    payload: Any, keys: tuple[str, ...]
) -> tuple[int | None, str]:
    """The id, and **which key carried it**.

    Added 2026-10-27, after the first real write. That call returned both ids
    and taught us nothing about Garmin's response shape, because the raw
    response is attached only on a miss -- so a success proved the candidate
    list was sufficient and left which candidate matched unknown, which is
    exactly what `test_write.py`'s `# invented:` labels were waiting on.

    **A response that only explains itself on failure cannot teach you anything
    when it works.** The key name travels on every call now. Key names are
    schema rather than health data -- the same argument `probe --explain` makes
    for printing paths and never values -- so this costs nothing to surface.
    """
    if not isinstance(payload, dict):
        return None, ""

    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):  # bool is an int; it is not an id
            continue
        if isinstance(value, int):
            return value, key

    for outer, value in payload.items():
        if isinstance(value, dict):
            found, key = _first_id_with_key(value, keys)
            if found is not None:
                return found, f"{outer}.{key}"
    return None, ""


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

    **A thin call into `_schedule_entry_on` since 2026-11-02.** The undo needs
    two more facts out of the same read -- how many entries matched, and which
    key carried each id -- and growing this function's return tuple would have
    changed the create path for the undo's benefit. One parser, two callers,
    and this signature unchanged: the create path cannot drift from the undo
    because there is nothing left to drift.
    """
    entry = _schedule_entry_on(payload, workout_id, day)
    return entry.schedule_id, entry.understood


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

    workout_id, workout_key = _first_id_with_key(written, WORKOUT_ID_KEYS)
    if workout_id is None:
        workout_id = plan.workout_id
        workout_key = "(from the name match, not the response)" if workout_id else ""
    outcome = Outcome(
        action=plan.action,
        workout_id=workout_id,
        scheduled=SCHEDULED_FAILED,
        date=day,
        requests_made=requests,
        id_keys={"workout_id": workout_key} if workout_key else {},
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
    outcome.scheduled_workout_id, schedule_key = _first_id_with_key(
        booked, SCHEDULE_ID_KEYS
    )
    if schedule_key:
        outcome.id_keys["scheduled_workout_id"] = schedule_key
    if outcome.scheduled_workout_id is None:
        outcome.raw = {"schedule_response": booked}
        outcome.warnings.append(
            "Scheduled, but the schedule id is not in a key this server "
            "recognises. Undo needs that id and not the workout id -- and since "
            "2026-11-02 that is no longer a dead end: `garmin_workout_unschedule` "
            "looks the id up from the calendar by name and date, so this entry "
            "can still be removed from a conversation. The raw response is "
            "included because the key name is still unmeasured."
        )
    return outcome


# --------------------------------------------------------------------------
# Undo: removing a calendar entry whose id nothing stored (§15.13, option 2)
# --------------------------------------------------------------------------
#
# **Why this is a lookup and not a ledger.** `unschedule_workout` takes the
# scheduled id and nothing else, and that id has been dropped by summarising
# clients five times -- a returned field is not a delivered one. §15.13 weighed
# three answers and ratified the one that stores nothing: Garmin's calendar
# already holds the fact, so an undo is a *read* followed by a delete. The cost
# is one extra request, paid only on an undo. The benefit is that a dropped id
# is recoverable rather than lost, and §4's token volume gains no account data.
#
# **What it refuses, and why each refusal is a different sentence.** "Already
# gone", "that date holds entries this server cannot read", and "the calendar is
# not a shape this server recognises" are three different facts with three
# different fixes, and a single "not found" would collapse them into the most
# dangerous one: a silent success reads as evidence the undo happened.


@dataclass(frozen=True)
class CalendarEntry:
    """What one calendar month says about one workout on one date.

    `matches` is why this is a dataclass rather than a tuple: the undo needs to
    tell "no entry" from "an entry whose ids we cannot read" from "two entries",
    and those are three different values of two fields.
    """

    schedule_id: int | None
    schedule_key: str
    workout_key: str
    #: Which of `CALENDAR_KEYS` held the list, and which date spelling matched.
    #: Recorded for the same reason the id keys are: this shape had no measured
    #: key names at all until the first live undo, and these two are still
    #: unmeasured because the undo reported the ids and nothing else. A success
    #: that teaches nothing is the mistake this module already made once.
    container_key: str
    date_key: str
    matches: int
    understood: bool


def _schedule_entry_on(payload: Any, workout_id: int, day: str) -> CalendarEntry:
    """**The one calendar parser.** Both the create path and the undo read this.

    The create path asks "is it already on that date" and the undo asks "which
    entry is on that date" -- the same read of the same unmeasured shape. Two
    parsers would mean a fix to one leaving the other wrong, so
    `_scheduled_ids_on` is now a thin call into this and keeps its old
    signature: the create path and its tests are untouched by construction.

    Records **which key carried each id**, because a calendar item is the one
    shape in this module with no measured key names at all. `WORKOUT_ID_KEYS`
    and `SCHEDULE_ID_KEYS` are confirmed for `upload_workout` and
    `schedule_workout` *responses*; against a calendar item they are still
    candidates. The first live undo settles them for free, the same way
    `id_keys` settled the write's.
    """
    if not isinstance(payload, dict):
        return CalendarEntry(None, "", "", "", "", 0, False)

    items = None
    container_key = ""
    for key in CALENDAR_KEYS:
        if key in payload:
            items = payload[key]
            container_key = key
            break
    if not isinstance(items, list):
        return CalendarEntry(None, "", "", "", "", 0, False)

    matches = []
    date_key = ""
    for item in items:
        if not isinstance(item, dict):
            continue
        if _first_id(item, CALENDAR_WORKOUT_ID_KEYS) != workout_id:
            continue
        for candidate in CALENDAR_DATE_KEYS:
            on = str(item.get(candidate) or "")
            if on.startswith(day):
                matches.append(item)
                date_key = candidate
                break

    if not matches:
        return CalendarEntry(None, "", "", container_key, "", 0, True)

    schedule_id, schedule_key = _first_id_with_key(matches[0], SCHEDULE_ID_KEYS)
    _, workout_key = _first_id_with_key(matches[0], CALENDAR_WORKOUT_ID_KEYS)
    return CalendarEntry(
        schedule_id,
        schedule_key,
        workout_key,
        container_key,
        date_key,
        len(matches),
        True,
    )


def _entries_on(payload: Any, day: str) -> int:
    """How many scheduled workouts that date holds, of any kind.

    Only ever used in a refusal message, and it earns its place there: "nothing
    called X is on 5 Nov" means something different when that date holds three
    other sessions than when it holds none. The first says the name or the date
    is wrong; the second says the whole month may be.
    """
    if not isinstance(payload, dict):
        return 0

    items = None
    for key in CALENDAR_KEYS:
        if key in payload:
            items = payload[key]
            break
    if not isinstance(items, list):
        return 0

    count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        for candidate in CALENDAR_DATE_KEYS:
            if str(item.get(candidate) or "").startswith(day):
                count += 1
                break
    return count


@dataclass
class UnscheduleOutcome:
    """A calendar entry that is gone, and everything the human needs after."""

    name: str
    date: str
    workout_id: int
    scheduled_workout_id: int
    removed: bool
    #: Entries for **this workout** still on that date. Normally 0. Garmin's own
    #: UI permits the same workout twice on one day, and `unschedule_workout`
    #: takes one id, so a duplicate is removed one call at a time.
    same_workout_left_on_date: int = 0
    #: Entries of **any** workout still on that date.
    #:
    #: Added 2026-11-02, after the first live undo. `remaining_on_date` counted
    #: only this workout and was named as though it counted the date: it read 0
    #: while another session was still there, and the refusal on the next call
    #: said the date "holds 1 in total". Two true numbers, one name, and the
    #: name belonged to the other one. Both are reported now, because an undo
    #: whose point is "that day was wrong" should say what is left on the day.
    entries_left_on_date: int = 0
    requests_made: int = 0
    warnings: list[str] = field(default_factory=list)
    id_keys: dict[str, str] = field(default_factory=dict)


class UnscheduleUncertain(GarminSourceError):
    """The delete was sent and its outcome is unknown.

    Deliberately **not** a `WriteRefused`, whose docstring promises the account
    is untouched -- a promise nothing can make once the request has left. A
    timeout on a DELETE may have been applied server-side. Naming the state
    "unknown" is the only honest answer, and it carries both ids so the human
    can settle it in Connect in one look.
    """


def unschedule(
    api: Any, tokens_path: str, name: str, day: str
) -> UnscheduleOutcome:
    """Take a session off the calendar, found by name and date (§15.13).

    **Name and date, never an id.** The id the delete needs is looked up here,
    so a conversation that lost it -- or never saw it, because the client
    summarised the write -- can still undo. It also means the model *cannot*
    pass a remembered id: the calendar is the current state and a remembered id
    is a memory of one, and where they disagree the calendar wins. Enforced by
    the signature rather than by a sentence.

    **The template is not deleted.** `unschedule_workout` removes the calendar
    entry and leaves the workout in the library, which is what makes it the
    right undo for a wrong *date*. Deleting the template is `delete_workout`,
    deliberately not exposed (§5's four permanent-deletion exclusions).

    Everything up to the delete refuses, because until then refusing is free.
    """
    # The cheapest refusal first: a bad date costs no request, and
    # `list_workouts` can cost ten.
    try:
        date_type.fromisoformat(day)
    except ValueError as exc:
        raise WriteRefused(
            f"{day!r} is not a YYYY-MM-DD date. Nothing was changed."
        ) from exc

    existing, requests = list_workouts(api, tokens_path)
    plan = resolve(name, existing)
    wanted = name.strip()

    if plan.action == UPLOADED or plan.workout_id is None:
        raise WriteRefused(
            f"no workout called {wanted!r} exists on this account, so nothing "
            f"on {day} can be removed under that name. Nothing was changed. "
            "Check the name against Garmin Connect -- the match is exact and "
            "case-sensitive, the same way a write resolves it."
        )

    year, month = int(day[:4]), int(day[5:7])
    try:
        with source._garmin_access(tokens_path):
            calendar = api.get_scheduled_workouts(year, month)
    except RateLimited:
        raise
    except Exception as exc:  # noqa: BLE001 - refusing is still free here
        raise WriteRefused(
            f"could not read the calendar for {year}-{month:02d} "
            f"({type(exc).__name__}), so the entry to remove could not be "
            "found. Nothing was changed."
        ) from exc
    requests += 1

    entry = _schedule_entry_on(calendar, plan.workout_id, day)
    total_on_date = _entries_on(calendar, day)

    if not entry.understood:
        raise WriteRefused(
            f"the calendar response for {year}-{month:02d} is not a shape this "
            f"server recognises, so whether {wanted!r} is on {day} cannot be "
            "established. Nothing was changed. **This is not the same as the "
            "entry being absent** -- it means the month could not be read at "
            "all, and the entry may well still be there."
        )

    if entry.matches == 0:
        # Spelled out rather than "workout(s)". This sentence is read aloud to a
        # human by a client, and 2026-11-02 is the day it was pasted back
        # verbatim -- a refusal is the one message guaranteed to be read
        # closely, so it should not look machine-assembled.
        if total_on_date == 0:
            holds = "Nothing else is scheduled on that date"
        elif total_on_date == 1:
            holds = "That date holds 1 other scheduled workout"
        else:
            holds = f"That date holds {total_on_date} other scheduled workouts"
        raise WriteRefused(
            f"nothing called {wanted!r} is on the calendar for {day}, so there "
            f"was nothing to remove and nothing was changed. {holds}. If the "
            "session was expected there, either it was never scheduled or it "
            "has already been removed."
        )

    if entry.schedule_id is None:
        raise WriteRefused(
            f"{wanted!r} IS on the calendar for {day}, but its entry carries no "
            "id in a key this server recognises, and `unschedule_workout` takes "
            "that id and nothing else. Nothing was changed. Remove it in Garmin "
            f"Connect; the workout id is {plan.workout_id}."
        )

    # ---- past this line the calendar can change, so nothing may refuse ------
    try:
        with source._garmin_access(tokens_path):
            api.unschedule_workout(entry.schedule_id)
    except RateLimited:
        raise
    except Exception as exc:  # noqa: BLE001 - the outcome is unknown, not safe
        raise UnscheduleUncertain(
            f"the request to remove entry {entry.schedule_id} from {day} failed "
            f"({type(exc).__name__}), and whether Garmin applied it is unknown. "
            f"Check {day} in Garmin Connect. The workout template "
            f"{plan.workout_id} is untouched either way."
        ) from exc
    requests += 1

    id_keys = {}
    if entry.schedule_key:
        id_keys["scheduled_workout_id"] = f"calendarItem.{entry.schedule_key}"
    if entry.workout_key:
        id_keys["workout_id"] = f"calendarItem.{entry.workout_key}"
    if entry.container_key:
        id_keys["calendar_container"] = entry.container_key
    if entry.date_key:
        id_keys["calendar_date"] = f"calendarItem.{entry.date_key}"

    outcome = UnscheduleOutcome(
        name=wanted,
        date=day,
        workout_id=plan.workout_id,
        scheduled_workout_id=entry.schedule_id,
        removed=True,
        same_workout_left_on_date=entry.matches - 1,
        # One entry went, and the calendar was read before it did. No second
        # request: subtracting the removal from the count we already have is
        # exact, and a re-read would cost a request to learn what we know.
        entries_left_on_date=max(total_on_date - 1, 0),
        requests_made=requests,
        id_keys=id_keys,
    )

    left = outcome.same_workout_left_on_date
    if left:
        remain = "1 still remains" if left == 1 else f"{left} still remain"
        outcome.warnings.append(
            f"{wanted!r} was on {day} {entry.matches} times and ONE entry was "
            f"removed, so {remain} on that date. Garmin permits the same "
            "workout twice on one day. Call this again with the same name and "
            "date to remove the next one."
        )

    return outcome
