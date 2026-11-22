"""`garmin_calendar` at the tool boundary (§5, v1.9-A).

The listing's own tests are in `test_scheduled.py`. This file covers what the
tool adds: the annotations, the ten-tool surface, the refusal reaching the
caller as a refusal rather than as a stack trace, and **the parity between the
dataclass and the wire model**.

Why parity gets a test of its own. The entry shape is declared twice -- a frozen
dataclass in `source/` and a pydantic model in `server.py` -- and nothing makes
them agree. The MCP surface is the thinnest consumer of any state in this
server and the one most likely to be forgotten, so a field added to the
dataclass and not to the model would simply never reach a conversation, with no
error anywhere.
"""

import asyncio
import dataclasses

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp.server import ScheduledEntryRow, garmin_calendar, mcp
from garmin_mcp.source import client as source
from garmin_mcp.source import scheduled

DAY = "2026-11-19"
NAME = "Thu threshold"
WORKOUT = 1750279892
ENTRY = 1829276077

# invented: the container key and the date key are still guesses; `workoutId`
# and `id` are the two the live undo of 2026-11-02 measured. Mirrors
# `test_scheduled.py` deliberately -- if a later call shows a different shape,
# both files change together.
CALENDAR = {
    "calendarItems": [
        {"workoutId": WORKOUT, "id": ENTRY, "title": NAME, "date": DAY}
    ]
}

# invented: the workout library, whose two key names the library's own code
# reads even though nothing here has read this payload.
LIBRARY = [{"workoutName": NAME, "workoutId": WORKOUT}]


class FakeApi:
    def __init__(self, calendar=None):
        self.calls: list[str] = []
        self.calendar = calendar if calendar is not None else CALENDAR

    def get_scheduled_workouts(self, year, month):
        self.calls.append("get_scheduled_workouts")
        return self.calendar

    def get_workouts(self, start=0, limit=100):
        self.calls.append("get_workouts")
        return list(LIBRARY) if start == 0 else []


@pytest.fixture
def api(monkeypatch, tokenfile):
    fake = FakeApi()
    monkeypatch.setattr(source, "_connect", lambda: fake)
    return fake


def registered(name):
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    return tools[name]


def test_the_tool_model_carries_every_field_the_dataclass_does():
    """One shape, two declarations, and this is what keeps them one shape."""
    assert {f.name for f in dataclasses.fields(scheduled.ScheduledEntry)} == set(
        ScheduledEntryRow.model_fields
    )


def test_the_calendar_read_is_honestly_read_only():
    annotations = registered("garmin_calendar").annotations

    assert annotations.read_only_hint is True
    assert annotations.destructive_hint is False
    assert annotations.idempotent_hint is True


def test_the_tenth_tool_does_not_perturb_the_nine():
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert names == {
        "garmin_auth_status",
        "garmin_training",
        "garmin_health",
        "garmin_activities",
        "garmin_zones",
        "garmin_calendar",
        "garmin_exercises",
        "garmin_workouts",
        "garmin_workout_create",
        "garmin_workout_unschedule",
    }


def test_the_tool_needs_only_a_start_date(api):
    result = garmin_calendar(DAY)

    assert len(result.entries) == 1
    assert result.entries[0].name == NAME
    assert result.entries[0].scheduled_workout_id == ENTRY
    assert result.months_read == 1
    assert result.requests_made == 1


def test_a_window_refusal_reaches_the_caller_as_a_tool_error(api):
    with pytest.raises(ToolError, match="runs backwards"):
        garmin_calendar("2026-11-19", "2026-11-12")

    assert api.calls == []


def test_an_unreadable_month_reaches_the_caller_as_a_tool_error(api):
    """And it must not read like an empty calendar at the boundary either --
    that is the whole point of refusing one layer down."""
    api.calendar = {"somethingElse": []}

    with pytest.raises(ToolError, match="shape this server cannot read"):
        garmin_calendar(DAY)


def test_the_keys_that_answered_reach_the_caller(api):
    """Schema rather than account data, by the same argument `probe --explain`
    makes -- and the two unmeasured spellings are settled by reading them."""
    result = garmin_calendar(DAY)

    assert result.keys_read["container"] == "calendarItems"
    assert result.keys_read["scheduled_workout_id"] == "id"
