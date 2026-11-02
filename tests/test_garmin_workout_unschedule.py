"""`garmin_workout_unschedule` at the tool boundary (§15.13, v1.7).

The lookup's own tests are in `test_unschedule.py`. This file covers what the
tool adds: the gate, the annotations -- this is the **first tool in this server
whose `destructive_hint` is honestly True** -- and the one sentence the result
exists to carry, which is that the workout itself survived.

Why that sentence gets a test of its own: an undo that reads as "the workout is
gone" produces a duplicate on the next write, because a write resolves by name
and the name is still taken. The failure would appear a week later, on a watch.
"""

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp.server import garmin_workout_unschedule, mcp
from garmin_mcp.source import client as source
from garmin_mcp.source import write

DAY = "2026-11-05"
NAME = "Thu threshold"
WORKOUT = 12345
ENTRY = 55555

# invented: the workout list. `workoutName` and `workoutId` are the keys the
# library itself reads; the item around them is a guess.
WORKOUTS = [{"workoutName": NAME, "workoutId": WORKOUT}]

# recorded in part: 2026-11-02, the first live undo. A calendar item spells the
# schedule id **`id`** and the template `workoutId`. The container key and the
# date key were not reported by that call, so `calendarItems` and `date` are
# still guesses -- `id_keys` records both from now on. Mirrors
# `test_unschedule.py` deliberately: if a later call shows a different shape,
# both files change together.
CALENDAR = {
    "calendarItems": [{"workoutId": WORKOUT, "date": DAY, "id": ENTRY}]
}

# recorded in part: the same workout twice on one day, which Garmin permits.
CALENDAR_TWICE = {
    "calendarItems": [
        {"workoutId": WORKOUT, "date": DAY, "id": ENTRY},
        {"workoutId": WORKOUT, "date": DAY, "id": 77777},
    ]
}


class FakeApi:
    def __init__(self, calendar=None):
        self.calls: list[str] = []
        self.calendar = calendar if calendar is not None else CALENDAR

    def get_workouts(self, start=0, limit=100):
        self.calls.append("get_workouts")
        return list(WORKOUTS) if start == 0 else []

    def get_scheduled_workouts(self, year, month):
        self.calls.append("get_scheduled_workouts")
        return self.calendar

    def unschedule_workout(self, scheduled_workout_id):
        self.calls.append("unschedule_workout")
        return None


@pytest.fixture
def api(monkeypatch, tokenfile):
    fake = FakeApi()
    monkeypatch.setattr(source, "_connect", lambda: fake)
    return fake


@pytest.fixture
def writes_on(monkeypatch):
    monkeypatch.setenv(write.WRITES_ENV, "1")


@pytest.fixture
def writes_off(monkeypatch):
    monkeypatch.delenv(write.WRITES_ENV, raising=False)


@pytest.fixture
def no_doorway(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("a refused undo must not open a Garmin request")

    monkeypatch.setattr(source, "_garmin_access", explode)
    monkeypatch.setattr(source, "_connect", explode)


def registered(name: str):
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    assert name in tools, f"{name} is not registered: {sorted(tools)}"
    return tools[name]


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_the_undo_is_refused_when_writes_are_off(writes_off, no_doorway):
    """Removing a calendar entry is a write. The same gate, refused at the same
    place -- before anything opens a request."""
    with pytest.raises(ToolError) as caught:
        garmin_workout_unschedule(NAME, DAY)

    assert write.WRITES_ENV in str(caught.value)


# --------------------------------------------------------------------------
# Annotations -- the §5 argument, now with a third tool
# --------------------------------------------------------------------------


def test_the_undo_is_the_first_honestly_destructive_tool(writes_on):
    """§5 asks for per-action honesty and `ToolAnnotations` are per tool, which
    is the whole reason the write lives in a tool of its own. This one removes
    something, so it says so -- and a client that puts a confirmation in front
    of destructive tools now puts it in front of the right call."""
    annotations = registered("garmin_workout_unschedule").annotations

    assert annotations.destructive_hint is True
    assert annotations.read_only_hint is False
    assert annotations.idempotent_hint is False


def test_the_create_tool_is_still_not_destructive():
    """The split is only honest if the other annotations stay true. Creating
    writes and schedules; it deletes nothing."""
    assert registered("garmin_workout_create").annotations.destructive_hint is False


def test_the_preview_tool_is_still_read_only():
    assert registered("garmin_workouts").annotations.read_only_hint is True


def test_the_seventh_tool_does_not_perturb_the_six():
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert {
        "garmin_auth_status",
        "garmin_training",
        "garmin_health",
        "garmin_exercises",
        "garmin_workouts",
        "garmin_workout_create",
        "garmin_workout_unschedule",
    } <= names


def test_the_tool_takes_a_name_and_a_date_and_no_id():
    """A.1 and B.4, enforced by the signature. The model may be holding a
    `scheduled_workout_id` from a create earlier in the conversation; the
    calendar is the current state and that id is a memory of one, so there must
    be no way to pass it."""
    schema = registered("garmin_workout_unschedule").input_schema

    assert set(schema["properties"]) == {"name", "date"}
    assert set(schema["required"]) == {"name", "date"}


# --------------------------------------------------------------------------
# What the result says
# --------------------------------------------------------------------------


def test_the_result_says_the_template_was_kept(api, writes_on):
    """The one fact this model exists to carry, in a field AND in prose. A
    returned field is not a delivered one -- five have been dropped by
    summarising clients -- so the note has to say it too."""
    result = garmin_workout_unschedule(NAME, DAY)

    assert result.removed is True
    assert result.template_kept is True
    assert "still exists" in result.note
    assert str(WORKOUT) in result.note


def test_the_result_carries_both_ids_and_the_date(api, writes_on):
    result = garmin_workout_unschedule(NAME, DAY)

    assert result.workout_id == WORKOUT
    assert result.scheduled_workout_id == ENTRY
    assert result.date == DAY
    assert result.name == NAME
    assert result.same_workout_left_on_date == 0
    assert result.entries_left_on_date == 0


def test_a_duplicate_entry_is_reported_in_the_note_as_well(monkeypatch, tokenfile, writes_on):
    """1.c. The remainder reaches the human through the same channel the undo
    itself does, because the field may never be shown."""
    fake = FakeApi(calendar=CALENDAR_TWICE)
    monkeypatch.setattr(source, "_connect", lambda: fake)

    result = garmin_workout_unschedule(NAME, DAY)

    assert result.same_workout_left_on_date == 1
    assert result.entries_left_on_date == 1
    assert "1 further entry" in result.note
    assert DAY in result.note
    assert result.warnings


def test_the_note_says_whether_the_day_is_now_empty(api, writes_on):
    """The ambiguity the first live undo surfaced, fixed where the human will
    actually read it. `same_workout_left_on_date` says whether to call again;
    this says whether the day is clear -- and the note carries both, because a
    field may never be shown."""
    result = garmin_workout_unschedule(NAME, DAY)

    assert "Nothing else is scheduled on " + DAY in result.note


def test_a_day_with_another_session_says_so_in_the_note(monkeypatch, tokenfile, writes_on):
    other = {
        "calendarItems": [
            {"workoutId": WORKOUT, "date": DAY, "id": ENTRY},
            {"workoutId": 999, "date": DAY, "id": 66666},
        ]
    }
    fake = FakeApi(calendar=other)
    monkeypatch.setattr(source, "_connect", lambda: fake)

    result = garmin_workout_unschedule(NAME, DAY)

    assert result.entries_left_on_date == 1
    assert "still holds 1 scheduled workout in total" in result.note


def test_a_refusal_reaches_the_client_as_a_tool_error(api, writes_on):
    """A refusal is a `GarminSourceError` inside and a `ToolError` at the
    boundary. Its text has to survive the translation -- the date in particular,
    because 'nothing to remove' without a date is not checkable."""
    with pytest.raises(ToolError) as caught:
        garmin_workout_unschedule("Not a workout on this account", DAY)

    assert "no workout called" in str(caught.value)
