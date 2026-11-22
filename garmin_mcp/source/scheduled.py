"""What is on the calendar: a read-only listing (§5, v1.9-A).

**Why this exists, dated.** Twice in one week the server could not say what was
on a date -- an unknown workout on Thursday 19 November and another on Saturday
21 November, neither identifiable. The only signal available was the **count
inside an unschedule refusal** (`write._entries_on` decorates it with "that date
holds N in total"), so the way to learn anything about the calendar was to call
a destructive tool and read what it refused to do. A write tool used as an
instrument is the finding this module closes.

**It adds no endpoint.** `get_scheduled_workouts(year, month)` has been called
by the create and undo paths since v1.5; `write.calendar_items_on` is the one
parser and this module is its third caller. What was missing was never a read,
only a surface.

**Why this takes `_garmin_access` directly instead of going through `fetch.py`.**
A calendar **month** is not a `SourceCall` keyed by one date -- `fetch._args_for`
knows `CALL_NONE` and `CALL_DATE` and raises on anything else by design -- and
there is no second metric sharing this payload to coalesce with. `write.py`,
`zones.py` and `activities.py` reached past the same seam for the same kind of
reason, and `tests/test_fetch_seam.py`'s allowlist had to gain a line on
purpose.

**Why the parser and the key names are imported from `write.py` rather than
moved here.** Their provenance comments are measurements with dates on them --
the 2026-11-02 undo settled `calendarItem.id` for the schedule id -- and moving
the constants would detach those comments from the path that made them. The
dependency is one way and there is no cycle. If a fourth consumer appears, the
parser moves down into a module of its own.

**The projection is an ALLOWLIST, and here the argument is stronger than it was
for activities.** An activity row's hundred keys were at least printed by step
0; a calendar item's shape has never been printed in full by anything. You
cannot write a denylist for fields you have not seen, so this module names what
may leave and drops the rest -- including whatever Garmin adds next.

**What is unmeasured here, and it is the headline field.** No code in this
repository had ever read a calendar item's **name**: both existing callers knew
which workout they meant and resolved the name through `get_workouts` first.
`workoutName` is asserted by the library for a workout *list item* and a workout
*detail* (`garminconnect/__init__.py:3406`), which is evidence about two
endpoints that are not this one. So the name is read from candidates, the
answering key is reported, a nameless entry falls back to **one** join against
the workout library, and a name is never invented. `name_basis` says which of
those happened on every single entry.

**A workout name is never logged.** It is user-written text and can carry a
place -- "Thursday threshold at <park>" -- so this module logs counts, months
and dates only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta
from typing import Any

from . import client as source
from .errors import GarminSourceError
from .write import (
    CALENDAR_KEYS,
    CALENDAR_WORKOUT_ID_KEYS,
    SCHEDULE_ID_KEYS,
    _first_id_with_key,
    calendar_items_on,
    list_workouts,
)

logger = logging.getLogger(__name__)

#: The widest window this tool will read. The cost here is **one request per
#: calendar month touched**, not per day, so 31 days is at most two requests --
#: the activity tool's reason for the same number (pagination the budget cannot
#: see) does not apply and the refusal says so. The number is kept the same
#: anyway, because one convention across the two window tools is easier to hold
#: than two, and widening it later is additive. Decided 2026-11-22.
MAX_WINDOW_DAYS = 31

#: Where a calendar item might carry its name. **Entirely unmeasured** -- order
#: is preference, not likelihood, and which one answered is reported on every
#: entry. `workoutName` sits in the middle because it is the spelling the
#: library asserts for a workout list item and a workout detail, which is
#: evidence about two endpoints that are not this one.
CALENDAR_NAME_KEYS = ("title", "workoutName", "name")

#: Where a calendar item might say which sport it is. Unmeasured, same rule: a
#: miss is `None` and never an inference from the name.
CALENDAR_SPORT_KEYS = ("sportTypeKey", "workoutSportTypeKey", "sportType")

#: Named so a test can assert on them rather than trusting the prose above.
#: Nothing here has been *seen* in a calendar item -- that is the point. The
#: allowlist is what protects the output; this list exists so the protection can
#: be asserted against the field names that would matter if they were there.
NEVER_RETURNED = (
    "ownerId",
    "ownerDisplayName",
    "ownerFullName",
    "ownerProfileImageUrlSmall",
    "ownerProfileImageUrlMedium",
    "ownerProfileImageUrlLarge",
    "userProfilePK",
    "userRoles",
    "privacy",
    "deviceId",
    "timeZoneId",
    "startLatitude",
    "startLongitude",
    "locationName",
)

NAME_FROM_ITEM = "the calendar item's own {key!r}"
NAME_FROM_LIBRARY = "the workout library, joined on workout_id"
NAME_NO_KEY = (
    "no name: none of the candidate keys answered and this entry carries no "
    "workout_id to look one up with"
)
NAME_NO_MATCH = (
    "no name: none of the candidate keys answered and the workout library has "
    "no template with this workout_id -- a deleted template still leaves its "
    "calendar entry"
)
NAME_LOOKUP_FAILED = (
    "no name: none of the candidate keys answered and the workout library could "
    "not be read on this call"
)

REQUESTS_MEASURED = (
    "measured: one request per calendar month this window touches, plus any "
    "workout-library pages read to resolve a name that the calendar item did "
    "not carry"
)

DATE_BASIS = (
    "Garmin's own calendar date for the entry, device-local, prefix-matched to "
    "YYYY-MM-DD -- some endpoints carry a time on it. No timezone arithmetic "
    "happens here"
)

NOTE = (
    "What is on the calendar, which is what was PLANNED -- `garmin_activities` "
    "says what was done. Every entry carries the `scheduled_workout_id` that "
    "removing or moving it needs, so nothing has to be remembered between "
    "calls. `name_basis` says where each name came from: the calendar item's "
    "own key names have never been measured, so a name read from the item "
    "reports which key answered, a name resolved from the workout library says "
    "so, and an entry with no name says that rather than guessing. An entry "
    "with no `workout_id` is still listed -- a course, a race or an event is "
    "exactly the kind of thing that could not be identified before this tool "
    "existed."
)

EMPTY_NOTE = (
    "Nothing is scheduled in this window. That is an answer, not a failure: the "
    "calendar container was read and held no entry on these dates. An "
    "unreadable month is refused instead of being reported as an empty one."
)


class CalendarWindowRefused(GarminSourceError):
    """A window we will not read. Refused before any request is made."""


class CalendarUnreadable(GarminSourceError):
    """The month came back in a shape this server cannot read.

    Deliberately **not** an empty answer. "Nothing is scheduled on Thursday" is
    a claim a human acts on by scheduling over whatever is actually there, and
    an empty month and an unrecognised payload look identical from the outside
    (`write.py`'s `CALENDAR_KEYS` comment). Preferring a bounded refusal to a
    silent partial is this repository's rule; here the partial would be a
    calendar.
    """


@dataclass(frozen=True)
class ScheduledEntry:
    """One calendar entry, reduced to what identifying or moving it needs."""

    date: str
    name: str | None
    name_basis: str
    workout_id: int | None
    scheduled_workout_id: int | None
    sport: str | None


@dataclass(frozen=True)
class ScheduledWindow:
    """Every entry in the window, and what reading it cost."""

    entries: tuple[ScheduledEntry, ...]
    months_read: int
    requests_made: int
    requests_basis: str
    date_basis: str
    keys_read: dict[str, str]
    warnings: tuple[str, ...]
    note: str


def _first_str_with_key(payload: Any, keys: tuple[str, ...]) -> tuple[str | None, str]:
    """The first non-empty string among `keys`, and which key carried it.

    The string twin of `write._first_id_with_key`, and it walks one level down
    for the same reason: Garmin wraps some values in a single-key envelope, and
    a nested `workout.workoutName` is the likeliest place a calendar item hides
    a name. A non-string is skipped rather than coerced -- `str()` on a dict
    would produce a "name" that is a Python repr.
    """
    if not isinstance(payload, dict):
        return None, ""

    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), key

    for outer, value in payload.items():
        if isinstance(value, dict):
            found, key = _first_str_with_key(value, keys)
            if found is not None:
                return found, f"{outer}.{key}"
    return None, ""


def _check_window(start_date: str, end_date: str) -> tuple[date_type, date_type]:
    """Refuse a window before spending a request on it.

    The library validates its own ints -- a year before 2000 or a month outside
    1 to 12 raises `ValueError` inside `get_scheduled_workouts` -- and it never
    sees a date string at all, because this module does the month arithmetic.
    An exception from in there would surface our own wiring fault dressed as a
    Garmin outcome, which is the mislabel `fetch._args_for` refuses to make.
    """
    try:
        first = date_type.fromisoformat(start_date)
        last = date_type.fromisoformat(end_date)
    except ValueError as exc:
        raise CalendarWindowRefused(
            f"{start_date!r} to {end_date!r} is not a pair of YYYY-MM-DD dates. "
            "Nothing was requested."
        ) from exc

    if last < first:
        raise CalendarWindowRefused(
            f"the window runs backwards: {start_date} is after {end_date}. "
            "Nothing was requested."
        )

    if first.year < 2000:
        raise CalendarWindowRefused(
            f"{start_date} is before the year 2000, which Garmin's calendar "
            "endpoint rejects. Nothing was requested."
        )

    span = (last - first).days + 1
    if span > MAX_WINDOW_DAYS:
        raise CalendarWindowRefused(
            f"{start_date} to {end_date} is {span} days, and this tool reads at "
            f"most {MAX_WINDOW_DAYS}. Ask for a narrower window: the calendar is "
            "read a whole month at a time, so a long window is several requests "
            "and 31 days is the convention the activity tool already uses. "
            "Nothing was requested."
        )
    return first, last


def _months_in(first: date_type, last: date_type) -> tuple[tuple[int, int], ...]:
    """Every (year, month) the window touches, in order.

    Carries the year with the month rather than iterating month numbers, so 28
    December to 3 January reads December and January rather than month 13.

    **One-indexed on purpose.** The library takes a 1-based month and subtracts
    one for Garmin's 0-indexed URL, so passing `month - 1` from here would read
    the previous month and look exactly like an empty calendar.
    """
    months = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append((year, month))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return tuple(months)


def _project(item: dict[str, Any], day: str) -> tuple[ScheduledEntry, dict[str, str]]:
    """One calendar item, reduced to the fields that may leave.

    An allowlist by construction: every field below is named, and an item key
    that is not named here cannot reach a response. Returns the entry and the
    key names that answered, so the caller can report them once per window.
    """
    keys: dict[str, str] = {}

    workout_id, workout_key = _first_id_with_key(item, CALENDAR_WORKOUT_ID_KEYS)
    schedule_id, schedule_key = _first_id_with_key(item, SCHEDULE_ID_KEYS)
    name, name_key = _first_str_with_key(item, CALENDAR_NAME_KEYS)
    sport, sport_key = _first_str_with_key(item, CALENDAR_SPORT_KEYS)

    if workout_key:
        keys["workout_id"] = workout_key
    if schedule_key:
        keys["scheduled_workout_id"] = schedule_key
    if name_key:
        keys["name"] = name_key
    if sport_key:
        keys["sport"] = sport_key

    return (
        ScheduledEntry(
            date=day,
            name=name,
            name_basis=(
                NAME_FROM_ITEM.format(key=name_key) if name else NAME_NO_KEY
            ),
            # A `workoutId` of 0 is not an id, the same rule `activities.py`
            # follows: reading one as an id would point at a template that does
            # not exist.
            workout_id=workout_id or None,
            scheduled_workout_id=schedule_id or None,
            sport=sport,
        ),
        keys,
    )


def _resolve_names(
    api: Any,
    tokens_path: str,
    entries: list[ScheduledEntry],
) -> tuple[list[ScheduledEntry], int, list[str]]:
    """Fill a missing name from the workout library, at most once per call.

    Decided 2026-11-22. The calendar item's own name key is unmeasured, so an
    entry with no readable name is the case this tool exists for -- and the
    template id it carries is enough to look the name up in a list whose key
    names the library's own code asserts. One walk, however many entries are
    missing a name, and never for an entry with no template id.

    **A failure here is a degraded name, never an exception.** `list_workouts`
    raises `WriteRefused` past its page cap, and a read tool surfacing a class
    whose docstring promises "nothing was written" would be a mislabel. The
    calendar rows are already in hand; raising would throw the answer away.
    """
    nameless = [
        entry
        for entry in entries
        if entry.name is None and entry.workout_id is not None
    ]
    if not nameless:
        return entries, 0, []

    try:
        library, requests = list_workouts(api, tokens_path)
    except Exception as exc:  # noqa: BLE001 - degraded, never raised: see above
        logger.info("workout library unreadable: %s", type(exc).__name__)
        unresolved = {id(entry) for entry in nameless}
        return (
            [
                _renamed(entry, None, NAME_LOOKUP_FAILED)
                if id(entry) in unresolved
                else entry
                for entry in entries
            ],
            0,
            [
                "A name could not be resolved for "
                f"{len(nameless)} entr{'y' if len(nameless) == 1 else 'ies'}: "
                "the workout library could not be read on this call. Every "
                "entry still carries its ids."
            ],
        )

    names: dict[int, str] = {}
    for workout in library:
        if not isinstance(workout, dict):
            continue
        workout_id = workout.get("workoutId")
        workout_name = workout.get("workoutName")
        if isinstance(workout_id, int) and isinstance(workout_name, str):
            names[workout_id] = workout_name.strip()

    filled = []
    for entry in entries:
        if entry.name is not None or entry.workout_id is None:
            filled.append(entry)
            continue
        found = names.get(entry.workout_id)
        if found:
            filled.append(_renamed(entry, found, NAME_FROM_LIBRARY))
        else:
            filled.append(_renamed(entry, None, NAME_NO_MATCH))

    return filled, requests, []


def _renamed(entry: ScheduledEntry, name: str | None, basis: str) -> ScheduledEntry:
    """A frozen entry with its name and that name's provenance replaced."""
    return ScheduledEntry(
        date=entry.date,
        name=name,
        name_basis=basis,
        workout_id=entry.workout_id,
        scheduled_workout_id=entry.scheduled_workout_id,
        sport=entry.sport,
    )


def read_window(
    start_date: str,
    end_date: str | None = None,
    tokens_path: str | None = None,
) -> ScheduledWindow:
    """What is scheduled between two dates, one request per month touched.

    `end_date` defaults to `start_date`, so a single day is the cheap common
    case and a window is the same call.

    **Each date is read from its own month's payload.** A month view may include
    neighbouring days -- never measured, and this is what makes it harmless:
    the same entry cannot arrive twice from two overlapping views, and a
    neighbouring day is never read out of the wrong month's payload.
    """
    first, last = _check_window(start_date, end_date or start_date)
    path = tokens_path or source.tokenstore_path()
    api = source._connect()

    requests = 0
    months: dict[tuple[int, int], Any] = {}
    for year, month in _months_in(first, last):
        with source._garmin_access(path):
            months[(year, month)] = api.get_scheduled_workouts(year, month)
        requests += 1

    keys: dict[str, str] = {}
    entries: list[ScheduledEntry] = []
    warnings: list[str] = []

    day = first
    while day <= last:
        iso = day.isoformat()
        found = calendar_items_on(months[(day.year, day.month)], iso)

        if not found.understood:
            raise CalendarUnreadable(
                f"the calendar for {day.year}-{day.month:02d} came back in a "
                "shape this server cannot read: none of the container keys "
                f"{', '.join(CALENDAR_KEYS)} is present. Refusing rather than "
                "reporting an empty calendar, because 'nothing is scheduled' "
                "is a claim you would act on."
            )

        if found.container_key:
            keys.setdefault("container", found.container_key)
        if found.date_key:
            keys.setdefault("date", found.date_key)

        for item in found.items:
            entry, item_keys = _project(item, iso)
            entries.append(entry)
            for key, value in item_keys.items():
                keys.setdefault(key, value)
            if entry.scheduled_workout_id is None:
                warnings.append(
                    f"the entry on {iso} carries no readable schedule id, so it "
                    "can be seen here but not moved or removed by this server. "
                    f"Keys tried: {', '.join(SCHEDULE_ID_KEYS)}."
                )

        day += timedelta(days=1)

    entries, name_requests, name_warnings = _resolve_names(api, path, entries)
    requests += name_requests
    warnings.extend(name_warnings)

    # Counts, months and dates only. An entry NAME is user text that can carry
    # a place.
    logger.info(
        "calendar %s..%s: %d month(s), %d entr(ies), %d request(s)",
        first.isoformat(),
        last.isoformat(),
        len(months),
        len(entries),
        requests,
    )

    return ScheduledWindow(
        entries=tuple(entries),
        months_read=len(months),
        requests_made=requests,
        requests_basis=REQUESTS_MEASURED,
        date_basis=DATE_BASIS,
        keys_read=keys,
        warnings=tuple(warnings),
        note=NOTE if entries else EMPTY_NOTE,
    )
