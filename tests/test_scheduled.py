"""Reading the calendar, with nothing reaching Garmin (§5, v1.9-A).

**Every calendar shape in this file is `# invented:`, and the fixtures are
deliberately awkward about it.** A calendar item's container key and date key
were never reported by the live undo that settled its ids, and its **name** key
has never been read by anything in this repository. So each fixture carries the
real SHAPE rather than a convenient one -- a container key, a date with a time
suffix on it, a nested dict, an unknown key Garmin might add tomorrow, and a
synthetic owner GUID -- because a fixture simple enough to be a one-key dict
cannot exercise the container search, the prefix match or the projection, and a
test written on one would report those paths as covered.

The rule this tool is built on: **an unreadable month is refused, never reported
as an empty one.** "Nothing is scheduled on Thursday" is a claim a human acts on
by scheduling over whatever is actually there.
"""

import pytest

from garmin_mcp.source import client as source
from garmin_mcp.source import scheduled
from garmin_mcp.source.scheduled import CalendarUnreadable, CalendarWindowRefused

#: The Thursday a workout sat on the calendar and could not be identified.
DAY = "2026-11-19"
NAME = "Thu threshold"
WORKOUT = 1750279892
ENTRY = 1829276077

# invented: a full item. `workoutId` and `id` are the two keys the live undo of
# 2026-11-02 measured (`calendarItem.id` is the SCHEDULE id, the third
# candidate); `title`, `date` and `calendarItems` are guesses, and the three
# fields after them are the ones that must never come out again.
ITEM_FULL = {
    "workoutId": WORKOUT,
    "id": ENTRY,
    "title": NAME,
    "date": DAY + "T00:00:00.0",
    "sportTypeKey": "running",
    "ownerDisplayName": "11111111-2222-4333-8444-555555555555",
    "ownerId": 987654,
    "startLatitude": 52.3702,
    "startLongitude": 4.8952,
    "locationName": "a park with a name in it",
    "somethingGarminAddsLater": {"nested": "value"},
}

# invented: an item with NO `workoutId` -- a course, a race, an event. This is
# the shape the write path's parser can never return, because both of its
# callers filter on a workout id they already know, and it is the likeliest
# shape of the entries that could not be identified on 19 and 21 November.
ITEM_NO_WORKOUT = {
    "id": 1829299999,
    "title": "Amsterdam 10k",
    "date": DAY + "T00:00:00.0",
}

# invented: an item carrying no schedule id at all. Visible, but nothing this
# server can move or remove.
ITEM_NO_SCHEDULE_ID = {
    "workoutId": 1750111111,
    "title": "Easy 40",
    "date": DAY + "T00:00:00.0",
}

# invented: an item with no name key anywhere, which is the case the workout
# library join exists for.
ITEM_NAMELESS = {
    "workoutId": WORKOUT,
    "id": ENTRY,
    "date": DAY + "T00:00:00.0",
}

# invented: a name one level down, which is where a wrapped payload would hide
# it. `_first_str_with_key` walks one level for the same reason the id reader
# does.
ITEM_NESTED_NAME = {
    "workoutId": 1750222222,
    "id": 1829222222,
    "date": DAY + "T00:00:00.0",
    "workout": {"workoutName": "Nested name"},
}

# invented: the container key exists and the list is empty -- a normal month
# with nothing in it.
MONTH_EMPTY = {"calendarItems": []}

# invented: and the pair with the one above is the point -- an empty month and a
# response whose container key is absent look identical and mean opposite
# things.
MONTH_UNREADABLE = {"somethingElse": [ITEM_FULL]}

# invented: the workout library. `workoutName` and `workoutId` are the two keys
# the library's own code reads (`garminconnect/__init__.py:3403`), so this half
# is sourced even though the calendar item beside it is not.
LIBRARY = [{"workoutName": NAME, "workoutId": WORKOUT}]


def month(*items):
    return {"calendarItems": list(items)}


class FakeApi:
    """Records every call, in order, with its arguments.

    The arguments are as much under test as the count: a month passed
    zero-indexed reads the month before and looks exactly like an empty
    calendar.
    """

    def __init__(self, months=None, workouts=None):
        self.calls: list[tuple] = []
        self.raises: dict[str, Exception] = {}
        self.months = dict(months or {(2026, 11): month(ITEM_FULL)})
        self.workouts = list(workouts if workouts is not None else LIBRARY)

    def _record(self, name, *args):
        self.calls.append((name, *args))
        if name in self.raises:
            raise self.raises[name]

    def get_scheduled_workouts(self, year, month_number):
        self._record("get_scheduled_workouts", year, month_number)
        return self.months.get((year, month_number), MONTH_EMPTY)

    def get_workouts(self, start=0, limit=100):
        self._record("get_workouts", start, limit)
        return self.workouts[start : start + limit]


@pytest.fixture
def api(monkeypatch, tokenfile):
    fake = FakeApi()
    monkeypatch.setattr(source, "_connect", lambda: fake)
    return fake


def names(fake):
    return [call[0] for call in fake.calls]


def read(start=DAY, end=None):
    return scheduled.read_window(start, end, str(source.tokenstore_path()))


# --------------------------------------------------------------------------
# Refusing before a request is spent
# --------------------------------------------------------------------------


def test_the_window_is_refused_when_the_dates_are_backwards(api):
    with pytest.raises(CalendarWindowRefused, match="runs backwards"):
        read("2026-11-19", "2026-11-12")

    assert api.calls == []


def test_a_window_wider_than_the_cap_is_refused_and_names_it(api):
    with pytest.raises(CalendarWindowRefused) as caught:
        read("2026-11-01", "2026-12-31")

    message = str(caught.value)
    assert "61 days" in message
    assert str(scheduled.MAX_WINDOW_DAYS) in message
    assert api.calls == []


def test_a_bad_date_is_refused_before_any_call(api):
    """The library never sees a date string here -- this module does the month
    arithmetic -- so nothing downstream would catch this one."""
    with pytest.raises(CalendarWindowRefused, match="not a pair of YYYY-MM-DD"):
        read("19 Nov", "2026-11-19")

    assert api.calls == []


def test_a_year_before_2000_is_refused_locally(api):
    """`get_scheduled_workouts` raises `ValueError` for it. An exception from
    inside the library would read as a Garmin outcome rather than as our own
    argument being wrong."""
    with pytest.raises(CalendarWindowRefused, match="before the year 2000"):
        read("1999-12-28", "1999-12-31")

    assert api.calls == []


# --------------------------------------------------------------------------
# Months: how many, which, and one-indexed
# --------------------------------------------------------------------------


def test_a_single_date_needs_no_end_date(api):
    window = read(DAY)

    assert window.months_read == 1
    assert window.requests_made == 1
    assert len(window.entries) == 1


def test_the_month_is_passed_one_indexed(api):
    """The library subtracts one for Garmin's 0-indexed URL
    (`garminconnect/__init__.py:3439`), so passing `month - 1` from here would
    read October and look like an empty November."""
    read(DAY)

    assert api.calls == [("get_scheduled_workouts", 2026, 11)]


def test_a_seven_day_window_across_a_month_boundary_reads_two_months(api):
    """The test `docs/SCOPE.md` asked for at v1.5 and no code could carry: the
    write path reads a single month, so a window spanning two had nothing to
    assert against until this tool existed."""
    api.months = {
        (2026, 11): month(ITEM_FULL | {"date": "2026-11-29T00:00:00.0"}),
        (2026, 12): month(ITEM_NO_WORKOUT | {"date": "2026-12-02T00:00:00.0"}),
    }

    window = read("2026-11-27", "2026-12-03")

    assert [call[1:] for call in api.calls if call[0] == "get_scheduled_workouts"] == [
        (2026, 11),
        (2026, 12),
    ]
    assert window.months_read == 2
    assert {entry.date for entry in window.entries} == {"2026-11-29", "2026-12-02"}


def test_a_window_across_a_year_boundary_rolls_the_year(api):
    api.months = {(2026, 12): MONTH_EMPTY, (2027, 1): MONTH_EMPTY}

    window = read("2026-12-28", "2027-01-03")

    assert [call[1:] for call in api.calls] == [(2026, 12), (2027, 1)]
    assert window.months_read == 2


def test_a_neighbouring_day_in_a_month_view_is_dropped(api):
    """A month view may include days either side -- never measured. Every date
    is read from its OWN month's payload, so a stray day cannot be reported."""
    api.months = {
        (2026, 11): month(
            ITEM_FULL,
            ITEM_NO_WORKOUT | {"date": "2026-12-01T00:00:00.0"},
        )
    }

    window = read(DAY)

    assert [entry.date for entry in window.entries] == [DAY]


def test_the_same_entry_in_two_month_views_is_returned_once(api):
    """Both months carry the 1 December entry. The window asks for 30 November
    to 2 December, so a loop over payload items would report it twice."""
    december_first = ITEM_FULL | {"date": "2026-12-01T00:00:00.0"}
    api.months = {
        (2026, 11): month(december_first),
        (2026, 12): month(december_first),
    }

    window = read("2026-11-30", "2026-12-02")

    assert [entry.date for entry in window.entries] == ["2026-12-01"]


# --------------------------------------------------------------------------
# What a listing must not drop
# --------------------------------------------------------------------------


def test_an_item_with_no_workout_id_is_listed_not_skipped(api):
    """The 19 and 21 November shape. The write path's parser filters on a
    workout id, so this item is invisible to it by construction -- and an entry
    nothing can identify is the whole reason this tool exists."""
    api.months = {(2026, 11): month(ITEM_NO_WORKOUT)}

    window = read(DAY)

    assert len(window.entries) == 1
    entry = window.entries[0]
    assert entry.workout_id is None
    assert entry.scheduled_workout_id == 1829299999
    assert entry.name == "Amsterdam 10k"


def test_an_entry_with_no_schedule_id_is_listed_with_a_warning(api):
    """Visible but not actionable. Saying so is the difference between an entry
    this server cannot move and an entry that is not there."""
    api.months = {(2026, 11): month(ITEM_NO_SCHEDULE_ID)}

    window = read(DAY)

    assert window.entries[0].scheduled_workout_id is None
    assert len(window.warnings) == 1
    assert DAY in window.warnings[0]


def test_the_same_workout_twice_on_one_day_is_two_entries(api):
    """Garmin's UI permits it and `unschedule_workout` takes one id, so the two
    are distinguishable only by their own schedule ids."""
    api.months = {
        (2026, 11): month(ITEM_FULL, ITEM_FULL | {"id": 1829277777}),
    }

    window = read(DAY)

    assert [entry.scheduled_workout_id for entry in window.entries] == [
        ENTRY,
        1829277777,
    ]


def test_a_zero_workout_id_is_not_an_id(api):
    """The §16.2a defect in a new place: a zero read as an id points at a
    template that does not exist."""
    api.months = {(2026, 11): month(ITEM_FULL | {"workoutId": 0})}

    assert read(DAY).entries[0].workout_id is None


# --------------------------------------------------------------------------
# Empty, and unreadable, which are not the same answer
# --------------------------------------------------------------------------


def test_an_empty_month_is_a_note_and_not_a_failure(api):
    api.months = {(2026, 11): MONTH_EMPTY}

    window = read(DAY)

    assert window.entries == ()
    assert window.note == scheduled.EMPTY_NOTE
    assert window.requests_made == 1


def test_an_unreadable_month_is_refused_rather_than_reported_empty(api):
    api.months = {(2026, 11): MONTH_UNREADABLE}

    with pytest.raises(CalendarUnreadable) as caught:
        read(DAY)

    message = str(caught.value)
    assert "2026-11" in message
    for key in scheduled.CALENDAR_KEYS:
        assert key in message


def test_a_payload_that_is_not_a_dict_is_unreadable_too(api):
    api.months = {(2026, 11): ["not", "a", "dict"]}

    with pytest.raises(CalendarUnreadable):
        read(DAY)


# --------------------------------------------------------------------------
# The projection, with its positive half
# --------------------------------------------------------------------------


def test_no_identity_or_location_or_unknown_field_survives_the_projection(api):
    """The positive half: `ITEM_FULL` carries a synthetic owner GUID, an owner
    id, coordinates, a location name and a key Garmin has not invented yet.
    The assertion is that none of them come out, and the fixture is what makes
    the assertion mean anything -- against an item with none of these, it would
    pass vacuously."""
    window = read(DAY)
    serialised = repr(window)

    for forbidden in scheduled.NEVER_RETURNED:
        assert forbidden not in serialised
    assert "somethingGarminAddsLater" not in serialised
    assert "a park with a name in it" not in serialised
    assert "11111111-2222-4333-8444-555555555555" not in serialised


def test_only_the_named_fields_leave(api):
    """An allowlist by construction, asserted as a set so a field added to the
    dataclass without a decision shows up here."""
    import dataclasses

    assert {f.name for f in dataclasses.fields(scheduled.ScheduledEntry)} == {
        "date",
        "name",
        "name_basis",
        "workout_id",
        "scheduled_workout_id",
        "sport",
    }


# --------------------------------------------------------------------------
# The name, which is the field nobody has ever measured
# --------------------------------------------------------------------------


def test_a_name_read_from_the_item_says_which_key_carried_it(api):
    entry = read(DAY).entries[0]

    assert entry.name == NAME
    assert "title" in entry.name_basis


def test_a_nested_name_is_found_one_level_down(api):
    api.months = {(2026, 11): month(ITEM_NESTED_NAME)}

    entry = read(DAY).entries[0]

    assert entry.name == "Nested name"
    assert entry.name_basis == scheduled.NAME_FROM_ITEM.format(
        key="workout.workoutName"
    )


def test_a_nameless_entry_is_resolved_from_the_workout_library(api):
    """Decided 2026-11-22: candidates first, then ONE join. The library's key
    names are the ones its own code reads, which is why the join is worth a
    request."""
    api.months = {(2026, 11): month(ITEM_NAMELESS)}

    window = read(DAY)

    assert window.entries[0].name == NAME
    assert window.entries[0].name_basis == scheduled.NAME_FROM_LIBRARY
    assert names(api) == ["get_scheduled_workouts", "get_workouts"]
    assert window.requests_made == 2


def test_the_library_is_walked_once_for_several_nameless_entries(api):
    api.months = {
        (2026, 11): month(ITEM_NAMELESS, ITEM_NAMELESS | {"id": 1829288888}),
    }

    window = read(DAY)

    assert names(api).count("get_workouts") == 1
    assert [entry.name for entry in window.entries] == [NAME, NAME]


def test_an_entry_with_no_workout_id_never_costs_a_library_read(api):
    """There is nothing to join on. Spending a request to prove that would be
    the kind of cost nobody can see from the response."""
    api.months = {(2026, 11): month(ITEM_NO_WORKOUT | {"title": None})}

    window = read(DAY)

    assert names(api) == ["get_scheduled_workouts"]
    assert window.entries[0].name is None
    assert window.entries[0].name_basis == scheduled.NAME_NO_KEY


def test_a_template_that_no_longer_exists_says_so(api):
    """A deleted template leaves its calendar entry behind."""
    api.months = {(2026, 11): month(ITEM_NAMELESS)}
    api.workouts = []

    window = read(DAY)

    assert window.entries[0].name is None
    assert window.entries[0].name_basis == scheduled.NAME_NO_MATCH


def test_a_library_read_that_fails_degrades_the_name_and_does_not_raise(api):
    """`list_workouts` raises `WriteRefused` past its page cap, and a read tool
    surfacing a class whose docstring promises 'nothing was written' would be a
    mislabel. The calendar rows are already in hand."""
    api.months = {(2026, 11): month(ITEM_NAMELESS, ITEM_NO_WORKOUT)}
    api.raises["get_workouts"] = RuntimeError("boom")

    window = read(DAY)

    nameless, race = window.entries
    assert nameless.name is None
    assert nameless.name_basis == scheduled.NAME_LOOKUP_FAILED
    # The entry that never had a template id keeps its own reason: it was never
    # waiting on the library.
    assert race.name_basis == scheduled.NAME_FROM_ITEM.format(key="title")
    assert len(window.warnings) == 1
    assert "could not be read" in window.warnings[0]


# --------------------------------------------------------------------------
# What the response says about itself
# --------------------------------------------------------------------------


def test_the_answering_keys_are_recorded(api):
    """Two of these spellings are still guesses, so every call reports which one
    answered -- the `id_keys` pattern, applied to the two the live undo did not
    report."""
    keys = read(DAY).keys_read

    assert keys["container"] == "calendarItems"
    assert keys["date"] == "date"
    assert keys["name"] == "title"
    assert keys["workout_id"] == "workoutId"
    assert keys["scheduled_workout_id"] == "id"
    assert keys["sport"] == "sportTypeKey"


def test_the_request_count_is_measured_at_each_doorway(api):
    """Counted where the requests happen, never computed from the month count --
    the library join happens afterwards and would be invisible."""
    api.months = {
        (2026, 11): month(ITEM_NAMELESS | {"date": "2026-11-29T00:00:00.0"}),
        (2026, 12): MONTH_EMPTY,
    }

    window = read("2026-11-29", "2026-12-02")

    assert window.months_read == 2
    # Two months, plus the one library page the nameless entry needed.
    assert window.requests_made == 3
    assert names(api) == [
        "get_scheduled_workouts",
        "get_scheduled_workouts",
        "get_workouts",
    ]
    assert "measured" in window.requests_basis


def test_the_name_is_never_logged(api, caplog):
    """An entry name is user-written text and can carry a place."""
    with caplog.at_level("INFO"):
        read(DAY)

    assert NAME not in caplog.text
    assert "a park with a name in it" not in caplog.text
