"""The undo path, with nothing reaching Garmin (§15.13 option 2, v1.7).

**Every calendar shape in this file is invented, and that is the point of the
tool it tests.** A calendar item's key names have never been measured: the
`workoutId` and `workoutScheduleId` that `id_keys` confirmed on 2026-10-31 were
confirmed for `upload_workout` and `schedule_workout` *responses*, and reading
them out of a calendar item is a third assumption wearing the same clothes. So
the cases that matter most here are the ones where an id cannot be read -- an
entry this server cannot parse must never come back looking like an entry that
is not there, because the second reads as evidence the undo already happened.

The rule the undo is built on, inherited from the write path and inverted:
**everything up to the delete refuses, and nothing after it does.** A refusal
is free while the calendar is untouched and dishonest afterwards.
"""

import pytest

from garmin_mcp.source import client as source
from garmin_mcp.source import write
from garmin_mcp.source.errors import RateLimited
from garmin_mcp.source.write import UnscheduleUncertain, WriteRefused

DAY = "2026-11-05"
NAME = "Thu threshold"
WORKOUT = 12345
ENTRY = 55555

# invented: the workout list. `workoutName` and `workoutId` are the two keys
# the library's own code reads (`garminconnect/__init__.py:3403`), so this half
# is sourced even though the surrounding item is not.
WORKOUTS = [{"workoutName": NAME, "workoutId": WORKOUT}]

# invented: a month holding the entry, plus a different workout on the same day.
# The second item is not decoration -- it is the assertion that a lookup matches
# on the workout id and not on "whatever is on that date".
CALENDAR_WITH_IT = {
    "calendarItems": [
        {"workoutId": WORKOUT, "date": DAY, "workoutScheduleId": ENTRY},
        {"workoutId": 999, "date": DAY, "workoutScheduleId": 66666},
    ]
}

# invented: the same workout twice on one date. Garmin's UI permits this, and
# `unschedule_workout` takes one id, so it takes two calls to clear.
CALENDAR_TWICE = {
    "calendarItems": [
        {"workoutId": WORKOUT, "date": DAY, "workoutScheduleId": ENTRY},
        {"workoutId": WORKOUT, "date": DAY, "workoutScheduleId": 77777},
    ]
}

# invented: that date holds sessions, none of them this one.
CALENDAR_OTHERS_ONLY = {
    "calendarItems": [
        {"workoutId": 999, "date": DAY, "workoutScheduleId": 66666},
        {"workoutId": 888, "date": DAY, "workoutScheduleId": 44444},
    ]
}

# invented: the entry is there and carries no id in any candidate key. The one
# case where the lookup can see the session and still cannot remove it.
CALENDAR_NO_ENTRY_ID = {
    "calendarItems": [{"workoutId": WORKOUT, "date": DAY}]
}

# invented: an empty month, which is a NORMAL month.
CALENDAR_EMPTY = {"calendarItems": []}

# invented: and the pair above is the point -- an empty month and a response
# whose container key is absent look identical and mean opposite things.
CALENDAR_UNREADABLE = {"somethingElse": [{"workoutId": WORKOUT, "date": DAY}]}


class FakeApi:
    """Records every call, in order. Order is as much under test as the
    arguments: the calendar read must happen before the delete, because it is
    the last point at which refusing is free."""

    def __init__(self, workouts=None, calendar=None):
        self.calls: list[tuple] = []
        self.raises: dict[str, Exception] = {}
        self.workouts = workouts if workouts is not None else list(WORKOUTS)
        self.calendar = calendar if calendar is not None else CALENDAR_WITH_IT

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

    def unschedule_workout(self, scheduled_workout_id):
        self._record("unschedule_workout", scheduled_workout_id)
        return None  # a DELETE; nothing may depend on parsing this

    def delete_workout(self, workout_id):  # pragma: no cover - must never run
        self._record("delete_workout", workout_id)


@pytest.fixture
def api(monkeypatch, tokenfile):
    fake = FakeApi()
    monkeypatch.setattr(source, "_connect", lambda: fake)
    return fake


def names(fake):
    return [call[0] for call in fake.calls]


def run(fake, name=NAME, day=DAY):
    return write.unschedule(fake, str(source.tokenstore_path()), name, day)


# --------------------------------------------------------------------------
# Refusing before anything can change
# --------------------------------------------------------------------------


def test_a_malformed_date_is_refused_before_any_request(api):
    """The cheapest refusal first. Resolving the name can cost ten requests and
    a date that cannot be parsed makes all of them pointless."""
    with pytest.raises(WriteRefused, match="not a YYYY-MM-DD"):
        run(api, day="5 Nov")

    assert api.calls == []


def test_an_unknown_name_is_refused_before_the_calendar_is_read(api):
    """`resolve` answers 'no match' with 'upload a new one', which is right for
    a write and wrong here. The undo must say so in its own words rather than
    inheriting a verb that makes no sense for it."""
    with pytest.raises(WriteRefused, match="no workout called"):
        run(api, name="Nothing of this name")

    assert names(api) == ["get_workouts"]


def test_two_templates_of_that_name_are_still_refused(api):
    """Inherited from `resolve` deliberately. Two *templates* sharing a name are
    different things and picking one is an invisible arbitrary choice -- unlike
    two calendar entries of one workout, which are interchangeable."""
    api.workouts = [
        {"workoutName": NAME, "workoutId": WORKOUT},
        {"workoutName": NAME, "workoutId": 67890},
    ]

    with pytest.raises(WriteRefused, match="2 workouts are already called"):
        run(api)

    assert "unschedule_workout" not in names(api)


def test_an_empty_name_is_refused(api):
    with pytest.raises(WriteRefused, match="needs a name"):
        run(api, name="   ")


def test_an_entry_already_gone_refuses_and_names_the_date(api):
    """§15.13's second inherited requirement, and the reason this tool exists
    in this shape: a silent success reads as evidence the undo happened."""
    api.calendar = CALENDAR_EMPTY

    with pytest.raises(WriteRefused) as caught:
        run(api)

    message = str(caught.value)
    assert DAY in message
    assert NAME in message
    assert "nothing was changed" in message.lower()
    assert "unschedule_workout" not in names(api)


def test_an_absent_entry_says_what_that_date_does_hold(api):
    """'Nothing called X is on 5 Nov' means something different when that date
    holds two other sessions than when it holds none: the first says the name
    or the date is wrong, the second says the whole month may be."""
    api.calendar = CALENDAR_OTHERS_ONLY

    with pytest.raises(WriteRefused, match="2 scheduled workout"):
        run(api)


def test_an_unreadable_calendar_is_not_an_absent_entry(api):
    """The two refusals must not be the same sentence. One means the entry is
    gone; the other means we cannot see, and the entry may well be there."""
    api.calendar = CALENDAR_UNREADABLE

    with pytest.raises(WriteRefused) as caught:
        run(api)

    message = str(caught.value)
    assert "not a shape this server recognises" in message
    assert "not the same as the entry being absent" in message
    assert "unschedule_workout" not in names(api)


def test_an_entry_whose_id_cannot_be_read_says_so_and_names_the_workout(api):
    """The one case where the session is visible and still not removable from
    here. It must not come back as 'already gone' -- that is the whole of
    finding A.4, and the key names it depends on are unmeasured."""
    api.calendar = CALENDAR_NO_ENTRY_ID

    with pytest.raises(WriteRefused) as caught:
        run(api)

    message = str(caught.value)
    assert "no id in a key this server recognises" in message
    assert str(WORKOUT) in message
    assert "unschedule_workout" not in names(api)


def test_a_calendar_read_that_fails_refuses_before_the_delete(api):
    api.raises["get_scheduled_workouts"] = RuntimeError("boom")

    with pytest.raises(WriteRefused, match="could not read the calendar"):
        run(api)

    assert "unschedule_workout" not in names(api)


def test_a_rate_limit_on_the_calendar_read_is_re_raised(api):
    """Not folded into the generic refusal. A 429 is 'come back later' and a
    refusal is 'this will never work'."""
    api.raises["get_scheduled_workouts"] = RateLimited("slow down")

    with pytest.raises(RateLimited):
        run(api)


# --------------------------------------------------------------------------
# The removal itself
# --------------------------------------------------------------------------


def test_the_entry_is_found_and_removed_and_both_ids_are_reported(api):
    outcome = run(api)

    assert names(api) == [
        "get_workouts",
        "get_scheduled_workouts",
        "unschedule_workout",
    ]
    assert api.calls[-1] == ("unschedule_workout", ENTRY)
    assert outcome.removed is True
    assert outcome.workout_id == WORKOUT
    assert outcome.scheduled_workout_id == ENTRY
    assert outcome.name == NAME
    assert outcome.date == DAY
    assert outcome.remaining_on_date == 0
    assert outcome.warnings == []
    assert outcome.requests_made == 3


def test_the_calendar_month_is_read_one_indexed(api):
    """The library subtracts one itself for Garmin's 0-indexed months
    (`__init__.py:3439`), so passing a 0-indexed month here would read the month
    before -- and find nothing, which looks exactly like an entry already gone."""
    run(api, day="2026-11-05")

    assert ("get_scheduled_workouts", 2026, 11) in api.calls


def test_another_workout_on_that_date_is_untouched(api):
    """The lookup matches on the workout id, never on 'what is on that date'."""
    outcome = run(api)

    assert outcome.scheduled_workout_id == ENTRY
    assert ("unschedule_workout", 66666) not in api.calls


def test_the_template_is_never_deleted(api):
    """`unschedule_workout` removes the calendar entry and leaves the workout in
    the library (`__init__.py:3472`). `delete_workout` is one of §5's four
    permanent-deletion exclusions and this path must never reach it."""
    run(api)

    assert "delete_workout" not in names(api)


def test_two_entries_of_the_same_workout_remove_one_and_say_another_remains(api):
    """**Decided 2026-11-02: notify, do not refuse.** Two calendar entries of one
    workout on one date are interchangeable -- the athlete sees one session
    either way -- so removing one is not a choice between different outcomes,
    and refusing would dead-end an undo on a state only Connect could fix.

    The remainder is said in prose as well as in a field, because a returned
    field is not a delivered one: five have been dropped by summarising clients.
    """
    api.calendar = CALENDAR_TWICE

    outcome = run(api)

    assert outcome.removed is True
    assert outcome.scheduled_workout_id == ENTRY
    assert outcome.remaining_on_date == 1
    assert outcome.warnings, "a remainder that only exists in a field is not delivered"
    assert "still remain" in outcome.warnings[0]
    assert DAY in outcome.warnings[0]


def test_the_removal_records_which_calendar_key_carried_each_id(api):
    """The calendar item is the last shape in this module with no measured key
    names. A successful undo is also the measurement -- the same argument that
    put `id_keys` on the write path, where a success used to teach nothing."""
    outcome = run(api)

    assert outcome.id_keys["scheduled_workout_id"] == "calendarItem.workoutScheduleId"
    assert outcome.id_keys["workout_id"] == "calendarItem.workoutId"


def test_a_later_candidate_key_is_recorded_as_itself(api):
    """The candidate list exists because nobody has read this shape. If the
    second candidate answers, the record must say the second candidate."""
    api.calendar = {
        "calendarItems": [
            {"workoutId": WORKOUT, "date": DAY, "scheduledWorkoutId": ENTRY}
        ]
    }

    outcome = run(api)

    assert outcome.id_keys["scheduled_workout_id"] == "calendarItem.scheduledWorkoutId"


def test_a_calendar_date_key_is_read_as_well_as_date(api):
    """Two spellings are tolerated because which one Garmin uses is unmeasured."""
    api.calendar = {
        "calendarItems": [
            {"workoutId": WORKOUT, "calendarDate": DAY, "workoutScheduleId": ENTRY}
        ]
    }

    assert run(api).scheduled_workout_id == ENTRY


def test_a_timestamped_date_still_matches_the_day(api):
    """Garmin's own dates carry a time on some endpoints. A prefix match is why
    a month view that includes neighbouring days cannot remove the wrong one."""
    api.calendar = {
        "calendarItems": [
            {
                "workoutId": WORKOUT,
                "date": f"{DAY}T00:00:00.0",
                "workoutScheduleId": ENTRY,
            }
        ]
    }

    assert run(api).scheduled_workout_id == ENTRY


# --------------------------------------------------------------------------
# After the request has left
# --------------------------------------------------------------------------


def test_a_failed_delete_is_uncertain_and_not_a_refusal(api):
    """`WriteRefused` promises the account is untouched, and nothing can promise
    that once the request has gone. A timeout on a DELETE may have been applied.
    'Unknown' is the only honest word, and it carries both ids so one look in
    Connect settles it."""
    api.raises["unschedule_workout"] = RuntimeError("gateway timeout")

    with pytest.raises(UnscheduleUncertain) as caught:
        run(api)

    message = str(caught.value)
    assert "unknown" in message.lower()
    assert str(ENTRY) in message
    assert str(WORKOUT) in message
    assert not isinstance(caught.value, WriteRefused)


def test_a_rate_limit_on_the_delete_is_re_raised_as_itself(api):
    """A 429 never reached Garmin's handler, so the entry is certainly still
    there -- which is more than 'unknown' and should not be flattened into it."""
    api.raises["unschedule_workout"] = RateLimited("slow down")

    with pytest.raises(RateLimited):
        run(api)


def test_nothing_is_parsed_out_of_the_delete_response(api):
    """It is a DELETE and its response shape is unknown. Reporting success on a
    field nobody has measured would make a working undo look like a failure."""
    outcome = run(api)

    assert outcome.removed is True


# --------------------------------------------------------------------------
# The shared parser (finding 7.a)
# --------------------------------------------------------------------------


def test_the_create_paths_calendar_answers_are_unchanged():
    """`_scheduled_ids_on` is now a thin call into `_schedule_entry_on`. Its
    three answers are the create path's contract and must be identical: an id, a
    readable month with nothing in it, and a month that is not readable at all.
    """
    assert write._scheduled_ids_on(CALENDAR_WITH_IT, WORKOUT, DAY) == (ENTRY, True)
    assert write._scheduled_ids_on(CALENDAR_EMPTY, WORKOUT, DAY) == (None, True)
    assert write._scheduled_ids_on(CALENDAR_UNREADABLE, WORKOUT, DAY) == (None, False)
    assert write._scheduled_ids_on(CALENDAR_OTHERS_ONLY, WORKOUT, DAY) == (None, True)
    assert write._scheduled_ids_on("not a dict", WORKOUT, DAY) == (None, False)


def test_counting_a_date_tolerates_a_shape_it_cannot_read():
    """`_entries_on` only ever decorates a refusal, so it must not raise on the
    shape that produced the refusal in the first place."""
    assert write._entries_on(CALENDAR_OTHERS_ONLY, DAY) == 2
    assert write._entries_on(CALENDAR_EMPTY, DAY) == 0
    assert write._entries_on(CALENDAR_UNREADABLE, DAY) == 0
    assert write._entries_on("not a dict", DAY) == 0
