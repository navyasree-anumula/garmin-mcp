"""The write path, with nothing reaching Garmin (§5, v1.5).

**The payload shapes in this file are invented, and that is the most important
thing about it.** `upload_workout` and `schedule_workout` have never been called
by this project, so nobody knows which keys their responses carry. The fixtures
below are labelled `# invented:` rather than `# recorded:` for exactly that
reason -- a citation this file cannot honestly give is one it must not fake, and
`test_fixture_provenance.py` accepts the honest label.

What that means for the tests: they prove our *handling* is right for each shape
we can imagine, not that any shape is the real one. So the cases that matter
most are the ones where we cannot read an id. The live cycle settles the keys,
and the first thing it should produce is a replacement for these labels.

The rule the whole module is built on: **once the workout exists, nothing
raises.** An exception after `upload_workout` loses the `workoutId`, leaving a
template on the account that is on no calendar -- and the next session reads the
calendar, so it would never find it.
"""

import pytest

from garmin_mcp.source import client as source
from garmin_mcp.source import write
from garmin_mcp.source.errors import RateLimited
from garmin_mcp.source.write import WriteRefused

DAY = "2026-11-05"

# invented: upload_workout has never been called; this is the shape its docstring
# implies (the created workout, echoed back). Replace after the live cycle.
UPLOAD_OK = {"workoutId": 12345, "workoutName": "Thu threshold"}

# invented: schedule_workout has never been called. SCOPE §5 says undo needs the
# scheduled id and not the workout id, so the key matters more than most.
SCHEDULE_OK = {"workoutScheduleId": 98765, "date": DAY}

# invented: the calendar month. The container key is the part that decides
# whether an empty month and an unreadable response can be told apart.
CALENDAR_WITH_IT = {
    "calendarItems": [
        {"workoutId": 12345, "date": DAY, "workoutScheduleId": 55555},
        {"workoutId": 999, "date": DAY, "workoutScheduleId": 66666},
    ]
}

# invented: the same container, empty. The pair matters more than either half:
# an empty month and an unreadable response are told apart by whether the key
# exists at all, and those two have opposite conclusions.
CALENDAR_EMPTY = {"calendarItems": []}

# invented: and not a Garmin response at all -- this is OUR composed shape,
# trimmed to the one field the write path reads (`workoutName`). The real
# structure is built by `compose.compose` and covered in `test_compose.py`;
# a full one here would make every assertion below harder to read for nothing.
STRUCTURE = {"workoutName": "Thu threshold", "workoutSegments": []}


class FakeApi:
    """Records every call, in order. Order is the thing under test as much as
    the arguments are: resolve must happen before the write, and the calendar
    read must happen before it too, because those are the last points at which
    refusing is free."""

    def __init__(self, workouts=None, calendar=None):
        self.calls: list[tuple] = []
        self.raises: dict[str, Exception] = {}
        self.workouts = workouts if workouts is not None else []
        self.calendar = calendar if calendar is not None else CALENDAR_EMPTY
        self.upload_response = UPLOAD_OK
        self.schedule_response = SCHEDULE_OK

    def _record(self, name, *args):
        self.calls.append((name, *args))
        if name in self.raises:
            raise self.raises[name]

    def get_workouts(self, start=0, limit=100):
        self._record("get_workouts", start, limit)
        return self.workouts[start : start + limit]

    def get_scheduled_workouts(self, year, month):
        self._record("get_scheduled_workouts", year, month)
        return self.calendar

    def upload_workout(self, structure):
        self._record("upload_workout", structure["workoutName"])
        return self.upload_response

    def update_workout(self, workout_id, structure):
        self._record("update_workout", workout_id, structure["workoutName"])
        return self.upload_response

    def schedule_workout(self, workout_id, day):
        self._record("schedule_workout", workout_id, day)
        return self.schedule_response


@pytest.fixture
def api(monkeypatch, tokenfile):
    fake = FakeApi()
    monkeypatch.setattr(source, "_connect", lambda: fake)
    return fake


@pytest.fixture
def tokens(tokenfile):
    """The token path, as a string, always under tmp.

    Never a bare relative path. The cross-process lock and the request budget
    are written next to the token file (`lock.py`, `ratelimit.py`), so passing
    `"tokens"` here puts `.garmin.lock` and `.garmin.ratelimit.json` in the
    repository root -- which is exactly how they first got staged for a commit.
    """
    return str(tokenfile)


def names(fake):
    return [call[0] for call in fake.calls]


def run(fake, structure=None, day=DAY):
    return write.create_and_schedule(
        fake, str(source.tokenstore_path()), structure or STRUCTURE, day
    )


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_writes_are_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv(write.WRITES_ENV, raising=False)
    assert write.writes_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " true "])
def test_the_gate_opens_on_one_or_true(monkeypatch, value):
    monkeypatch.setenv(write.WRITES_ENV, value)
    assert write.writes_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "yes", "on", "enabled", "True-ish"])
def test_a_near_miss_does_not_open_the_gate(monkeypatch, value):
    """A gate that accepts "yes" is a gate whose state nobody can predict, and
    the thing on the other side of it writes to a real training calendar."""
    monkeypatch.setenv(write.WRITES_ENV, value)
    assert write.writes_enabled() is False


def test_the_refusal_names_the_variable_and_the_restart():
    """A refusal that does not say how to fix it costs a round trip, and this
    one also has to mention the restart -- the client caches tool schemas at
    session start, so setting the variable alone changes nothing."""
    message = str(write.WritesDisabled())

    assert write.WRITES_ENV in message
    assert "restart" in message.lower()
    assert "nothing was sent" in message.lower()


# --------------------------------------------------------------------------
# Idempotency: name match means update in place
# --------------------------------------------------------------------------


def test_no_match_uploads():
    assert write.resolve("Thu threshold", []) == write.Resolution(write.UPLOADED)


def test_one_match_updates_that_id():
    existing = [{"workoutName": "Thu threshold", "workoutId": 7}]

    assert write.resolve("Thu threshold", existing) == write.Resolution(
        write.UPDATED, 7
    )


def test_the_match_ignores_surrounding_whitespace():
    existing = [{"workoutName": "  Thu threshold  ", "workoutId": 7}]

    assert write.resolve("Thu threshold", existing).action == write.UPDATED


def test_the_match_is_case_sensitive():
    """Garmin keeps the case the human typed, so two names differing only in
    case are two names. Folding them would update a workout the human did not
    point at."""
    existing = [{"workoutName": "thu threshold", "workoutId": 7}]

    assert write.resolve("Thu threshold", existing).action == write.UPLOADED


def test_two_matches_are_refused_rather_than_resolved():
    """Picking one would be arbitrary AND invisible: the response would say
    'updated' and look entirely correct either way."""
    existing = [
        {"workoutName": "Thu threshold", "workoutId": 7},
        {"workoutName": "Thu threshold", "workoutId": 8},
    ]

    with pytest.raises(WriteRefused, match="2 workouts are already called"):
        write.resolve("Thu threshold", existing)


def test_a_match_with_no_readable_id_is_refused():
    """It cannot be updated in place, and uploading instead would leave two
    workouts of one name -- the state the match exists to prevent."""
    with pytest.raises(WriteRefused, match="no readable id"):
        write.resolve("Thu threshold", [{"workoutName": "Thu threshold"}])


def test_an_empty_name_is_refused():
    with pytest.raises(WriteRefused, match="needs a name"):
        write.resolve("   ", [])


# --------------------------------------------------------------------------
# Paging, because the default limit is 100
# --------------------------------------------------------------------------


def test_a_short_first_page_costs_one_request(api, tokens):
    api.workouts = [{"workoutName": f"w{i}", "workoutId": i} for i in range(5)]

    found, requests = write.list_workouts(api, tokens)

    assert len(found) == 5
    assert requests == 1


def test_a_full_page_is_followed_by_another(api, tokens):
    """150 workouts. One call would return the first 100 and a name at position
    101 would look absent -- so the re-prescription would upload a duplicate
    instead of updating in place, which is the whole failure the name
    convention exists to prevent."""
    api.workouts = [{"workoutName": f"w{i}", "workoutId": i} for i in range(150)]

    found, requests = write.list_workouts(api, tokens)

    assert len(found) == 150
    assert requests == 2
    assert [call[1] for call in api.calls] == [0, 100]


def test_an_exact_multiple_of_the_page_size_asks_once_more(api, tokens):
    """200 workouts returns two full pages, and a full page is indistinguishable
    from 'there may be more'. The third call is what proves there is not."""
    api.workouts = [{"workoutName": f"w{i}", "workoutId": i} for i in range(200)]

    found, requests = write.list_workouts(api, tokens)

    assert len(found) == 200
    assert requests == 3


def test_the_page_cap_refuses_instead_of_matching_a_partial_list(api, tokens, monkeypatch):
    monkeypatch.setattr(write, "MAX_PAGES", 2)
    api.workouts = [{"workoutName": f"w{i}", "workoutId": i} for i in range(500)]

    with pytest.raises(WriteRefused, match="more than 200 workouts"):
        write.list_workouts(api, tokens)


def test_a_non_list_from_get_workouts_is_refused(api, tokens):
    """A dict where a list belongs would make `resolve` iterate over keys and
    find no match -- so it would upload a duplicate rather than fail."""
    api.get_workouts = lambda start=0, limit=100: {"not": "a list"}

    with pytest.raises(WriteRefused, match="not a list"):
        write.list_workouts(api, tokens)


# --------------------------------------------------------------------------
# The happy paths
# --------------------------------------------------------------------------


def test_a_fresh_name_uploads_then_schedules_and_never_reads_the_calendar(api):
    """A workout that does not exist cannot already be scheduled, so the
    calendar read would buy nothing and is skipped."""
    outcome = run(api)

    assert names(api) == ["get_workouts", "upload_workout", "schedule_workout"]
    assert outcome.action == write.UPLOADED
    assert outcome.workout_id == 12345
    assert outcome.scheduled == write.SCHEDULED_NEW
    assert outcome.scheduled_workout_id == 98765
    assert outcome.date == DAY
    assert outcome.requests_made == 3
    assert outcome.warnings == []


def test_a_known_name_updates_in_place_and_keeps_the_id(api):
    api.workouts = [{"workoutName": "Thu threshold", "workoutId": 12345}]

    outcome = run(api)

    assert names(api) == [
        "get_workouts",
        "get_scheduled_workouts",
        "update_workout",
        "schedule_workout",
    ]
    assert ("update_workout", 12345, "Thu threshold") in api.calls
    assert "upload_workout" not in names(api)
    assert outcome.action == write.UPDATED
    assert outcome.requests_made == 4


def test_the_calendar_month_is_read_one_indexed(api):
    """`get_scheduled_workouts` takes a 1-indexed month and converts to Garmin's
    0-indexed one internally (`__init__.py`). Passing the converted value would
    read the wrong month and conclude nothing was scheduled."""
    api.workouts = [{"workoutName": "Thu threshold", "workoutId": 12345}]

    run(api)

    assert ("get_scheduled_workouts", 2026, 11) in api.calls


def test_an_update_already_on_that_date_does_not_schedule_again(api):
    """The id survives an update, so the existing calendar entry still points at
    it. Scheduling again would put the same session on the watch twice."""
    api.workouts = [{"workoutName": "Thu threshold", "workoutId": 12345}]
    api.calendar = CALENDAR_WITH_IT

    outcome = run(api)

    assert "schedule_workout" not in names(api)
    assert outcome.scheduled == write.SCHEDULED_ALREADY
    assert outcome.scheduled_workout_id == 55555, "the existing entry's id, for undo"


def test_another_workout_on_that_date_is_not_mistaken_for_this_one(api):
    """The calendar carries everybody's sessions. Matching on the date alone
    would skip scheduling because something else is on that day."""
    api.workouts = [{"workoutName": "Thu threshold", "workoutId": 12345}]
    api.calendar = {
        "calendarItems": [{"workoutId": 999, "date": DAY, "workoutScheduleId": 66666}]
    }

    outcome = run(api)

    assert outcome.scheduled == write.SCHEDULED_NEW


def test_the_same_workout_on_a_different_date_still_schedules(api):
    api.workouts = [{"workoutName": "Thu threshold", "workoutId": 12345}]
    api.calendar = {
        "calendarItems": [
            {"workoutId": 12345, "date": "2026-11-12", "workoutScheduleId": 77777}
        ]
    }

    outcome = run(api)

    assert outcome.scheduled == write.SCHEDULED_NEW


# --------------------------------------------------------------------------
# Refusals, all of them before anything is written
# --------------------------------------------------------------------------


def test_a_malformed_date_is_refused_before_any_request(api):
    with pytest.raises(WriteRefused, match="not a YYYY-MM-DD date"):
        run(api, day="5 November")

    assert api.calls == [], "refused before the account was touched"


def test_an_unreadable_calendar_refuses_before_writing(api):
    """An empty month and an unreadable response look identical and have
    opposite conclusions. Refusing is free here; after the write it is not."""
    api.workouts = [{"workoutName": "Thu threshold", "workoutId": 12345}]
    api.calendar = {"somethingElse": True}

    with pytest.raises(WriteRefused, match="not a shape this server recognises"):
        run(api)

    assert "update_workout" not in names(api)


def test_a_calendar_read_that_fails_refuses_before_writing(api):
    api.workouts = [{"workoutName": "Thu threshold", "workoutId": 12345}]
    api.raises["get_scheduled_workouts"] = RuntimeError("boom")

    with pytest.raises(WriteRefused, match="could not read the calendar"):
        run(api)

    assert "update_workout" not in names(api)


def test_an_empty_calendar_month_is_not_an_unreadable_one(api):
    """The container key exists and is empty: a normal month with nothing in it.
    Treating that as unreadable would refuse every legitimate re-prescription."""
    api.workouts = [{"workoutName": "Thu threshold", "workoutId": 12345}]
    api.calendar = CALENDAR_EMPTY

    assert run(api).scheduled == write.SCHEDULED_NEW


def test_a_rate_limit_on_the_calendar_read_is_re_raised(api):
    """§6 forbids retrying into a throttle, and nothing has been written yet, so
    the 429 travels rather than becoming a report."""
    api.workouts = [{"workoutName": "Thu threshold", "workoutId": 12345}]
    api.raises["get_scheduled_workouts"] = RateLimited("slow down")

    with pytest.raises(RateLimited):
        run(api)


# --------------------------------------------------------------------------
# After the write, nothing raises
# --------------------------------------------------------------------------


def test_a_failed_schedule_reports_the_orphan_and_does_not_raise(api):
    """The expensive failure. The template exists on Garmin and is on no
    calendar, so nothing that reads the calendar will ever show it -- including
    this server. An exception here would lose the id with it."""
    api.raises["schedule_workout"] = RuntimeError("boom")

    outcome = run(api)

    assert outcome.workout_id == 12345, "the id survives the failure"
    assert outcome.scheduled == write.SCHEDULED_FAILED
    assert outcome.warnings
    assert "on no calendar" in outcome.warnings[0]
    assert "12345" in outcome.warnings[0]


def test_a_rate_limit_after_the_write_is_reported_not_raised(api):
    """§6 says never retry into a throttle; §5 says a partial write reports what
    it got to. After the write the second rule wins, because the id is the only
    thing that can find the orphan."""
    api.raises["schedule_workout"] = RateLimited("slow down")

    outcome = run(api)

    assert outcome.scheduled == write.SCHEDULED_FAILED
    assert outcome.workout_id == 12345
    assert "RateLimited" in outcome.warnings[0]


def test_an_unreadable_workout_id_reports_the_raw_response(api):
    """The write happened and we cannot name what was written. Scheduling is
    impossible, so it is not attempted, and the raw response goes back so the
    key can be found by hand."""
    api.upload_response = {"somethingNew": {"nested": "no id here"}}

    outcome = run(api)

    assert outcome.workout_id is None
    assert "schedule_workout" not in names(api), "cannot schedule what we cannot name"
    assert outcome.raw == {"write_response": api.upload_response}
    assert "cannot be undone" in outcome.warnings[0]


def test_an_unreadable_schedule_id_still_reports_success_and_the_gap(api):
    """Scheduled, but undo is unavailable: `unschedule_workout` takes the
    schedule id and not the workout id, so losing it costs the only rollback."""
    api.schedule_response = {"ok": True}

    outcome = run(api)

    assert outcome.scheduled == write.SCHEDULED_NEW
    assert outcome.scheduled_workout_id is None
    assert outcome.raw == {"schedule_response": {"ok": True}}
    assert "Undo needs that id" in outcome.warnings[0]


def test_an_id_in_a_nested_envelope_is_found(api):
    """Garmin wraps some responses in a single-key envelope. Tolerating one
    level costs nothing and saves an orphan report for a write that worked."""
    api.upload_response = {"workout": {"workoutId": 4242}}

    assert run(api).workout_id == 4242


def test_a_boolean_is_not_mistaken_for_an_id(api):
    """`True` is an `int` in Python, so a response carrying `{"id": true}` would
    otherwise schedule workout number 1."""
    api.upload_response = {"id": True, "workout": {"workoutId": 4242}}

    assert run(api).workout_id == 4242


def test_an_update_whose_response_carries_no_id_falls_back_to_the_known_one(api):
    """On the update path the id was already known from the match, so a silent
    response does not cost it. Only an upload can genuinely lose the id."""
    api.workouts = [{"workoutName": "Thu threshold", "workoutId": 12345}]
    api.upload_response = {"ok": True}

    outcome = run(api)

    assert outcome.workout_id == 12345
    assert outcome.scheduled == write.SCHEDULED_NEW
