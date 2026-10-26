"""`garmin_workout_create` at the tool boundary (§5, v1.5).

The orchestration's own tests are in `test_write.py`. This file covers what the
tool adds: the gate, the approval marker, and the fact that every refusal
happens **before** a request rather than after one.

The gate is why the whole file has to set an environment variable to get
anything done. That friction is the feature: an image that reaches a laptop
cannot write to a real training calendar until somebody edits the client config,
and a test suite that did not have to opt in would be a test suite that could
not tell whether the gate worked.
"""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp.server import (
    WorkoutStep,
    garmin_auth_status,
    garmin_workout_create,
    garmin_workouts,
    mcp,
)
from garmin_mcp.source import client as source
from garmin_mcp.source import compose as compose_module
from garmin_mcp.source import write

DAY = "2026-11-05"

# invented: ours, not Garmin's -- the step list a human would ask for. Labelled
# because the rule in `test_fixture_provenance.py` is deliberately simple: in a
# file that carries payload shapes, every module-level shape says where it came
# from. A rule with a category exemption is a rule people argue with.
EASY_RUN = [WorkoutStep(kind="interval", duration_secs=2400.0)]


# invented: upload_workout and schedule_workout have never been called, so these
# keys are a guess. Mirrors test_write.py's fixtures deliberately -- if the live
# cycle shows a different shape, both files change together.
UPLOAD_OK = {"workoutId": 12345}
SCHEDULE_OK = {"workoutScheduleId": 98765}


class FakeApi:
    def __init__(self):
        self.calls: list[str] = []

    def get_workouts(self, start=0, limit=100):
        self.calls.append("get_workouts")
        return []

    def upload_workout(self, structure):
        self.calls.append("upload_workout")
        return UPLOAD_OK

    def schedule_workout(self, workout_id, day):
        self.calls.append("schedule_workout")
        return SCHEDULE_OK


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
    """Proves a refusal cost nothing. Any request at all raises here, so a test
    using this fixture asserts the absence of requests structurally rather than
    by counting them."""

    def explode(*args, **kwargs):
        raise AssertionError("a refused write must not open a Garmin request")

    monkeypatch.setattr(source, "_garmin_access", explode)
    monkeypatch.setattr(source, "_connect", explode)


def previewed(name="Easy 40", steps=None):
    """A structure and its marker, straight from the preview -- the only
    supported way to get one. Building the pair by hand in each test would
    bypass the very handoff under test."""
    preview = garmin_workouts("preview", name, steps or EASY_RUN)
    return preview.structure, preview.approval


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_the_write_is_refused_when_writes_are_disabled(writes_off, no_doorway):
    structure, approval = previewed()

    with pytest.raises(ToolError, match=write.WRITES_ENV):
        garmin_workout_create(structure, approval, DAY)


def test_the_refusal_comes_before_the_structure_is_even_validated(
    writes_off, no_doorway
):
    """Order matters: the gate is checked first, so a disabled server gives the
    same answer for a good structure and a bad one. Validating first would leak
    whether a structure would have been accepted."""
    with pytest.raises(ToolError, match=write.WRITES_ENV):
        garmin_workout_create({"junk": True}, "sha256:nope", DAY)


def test_auth_status_reports_writes_disabled(writes_off, fake_garmin, tokenfile):
    assert garmin_auth_status().writes_enabled is False


def test_auth_status_reports_writes_enabled(writes_on, fake_garmin, tokenfile):
    """The 7.b question -- "are writes enabled right now" -- answered in the one
    call an agent already makes before anything else."""
    assert garmin_auth_status().writes_enabled is True


def test_the_status_and_the_refusal_read_the_same_source(monkeypatch):
    """Pass A.3: two consumers of one piece of state. If one cached the value at
    import and the other read it live, the agent could be told writes are on and
    then be refused."""
    monkeypatch.setenv(write.WRITES_ENV, "1")
    assert write.writes_enabled() is True
    monkeypatch.delenv(write.WRITES_ENV)
    assert write.writes_enabled() is False


# --------------------------------------------------------------------------
# The approval marker
# --------------------------------------------------------------------------


def test_a_previewed_structure_with_its_marker_is_written(writes_on, api):
    structure, approval = previewed()

    result = garmin_workout_create(structure, approval, DAY)

    assert result.action == "uploaded"
    assert result.workout_id == 12345
    assert result.scheduled == "new"
    assert result.scheduled_workout_id == 98765
    assert result.date == DAY
    assert result.warnings == []


def test_a_structure_edited_after_the_preview_is_refused(writes_on, no_doorway):
    """The failure §5 names: the human approves one thing and something else
    lands. Changing the name is the gentlest possible edit and it still has to
    fail -- a session renamed in flight would resolve against a different
    existing workout."""
    structure, approval = previewed()
    structure["workoutName"] = "Something else"

    with pytest.raises(ToolError, match="does not match its approval marker"):
        garmin_workout_create(structure, approval, DAY)


def test_a_recomposed_structure_is_refused_even_when_it_is_identical_in_spirit(
    writes_on, no_doorway
):
    """A preview of a *different* session produces a different marker, so
    pairing one session's structure with another's approval fails. This is the
    stale-structure case: two previews in one conversation, and the wrong pair
    sent."""
    first_structure, _ = previewed("Easy 40")
    _, second_approval = previewed("Thu threshold")

    with pytest.raises(ToolError, match="does not match its approval marker"):
        garmin_workout_create(first_structure, second_approval, DAY)


def test_whitespace_around_the_marker_is_tolerated(writes_on, api):
    """A client that pretty-prints its JSON should not invalidate an approval by
    padding a string."""
    structure, approval = previewed()

    assert garmin_workout_create(structure, f"  {approval}\n", DAY).workout_id == 12345


def test_a_structure_that_fails_validation_is_refused(writes_on, no_doorway):
    """Validated again on the way in, not only at preview time. The marker
    proves the structure is the one that was shown; validation proves the one
    that was shown is still something we are willing to send."""
    with pytest.raises(ToolError, match="Nothing was sent to Garmin"):
        garmin_workout_create({"workoutName": "x"}, "sha256:whatever", DAY)


def test_the_preview_and_the_create_agree_on_the_marker(writes_on, api):
    """The handoff, asserted as a round trip rather than as two independent
    facts -- which is what it is in practice."""
    preview = garmin_workouts("preview", "Easy 40", EASY_RUN)

    assert preview.approval == compose_module.approval_for(preview.structure)
    assert garmin_workout_create(
        preview.structure, preview.approval, DAY
    ).action == "uploaded"


# --------------------------------------------------------------------------
# What the schema and the annotations tell a client
# --------------------------------------------------------------------------


def registered(name: str):
    import asyncio

    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    assert name in tools, f"{name} is not registered: {sorted(tools)}"
    return tools[name]


def test_the_write_tool_is_annotated_as_a_write():
    """Not read-only and not idempotent, because it is neither: calling it twice
    with the same date resolves the second call to an update, which is a
    different act from the first."""
    annotations = registered("garmin_workout_create").annotations

    assert annotations.read_only_hint is False
    assert annotations.idempotent_hint is False


def test_the_write_tool_is_not_marked_destructive():
    """§5: create and schedule are not destructive -- nothing is deleted, the
    four permanent-deletion exclusions are untouched, and `delete_workout` stays
    available for the human. Marking it destructive would put a confirmation in
    front of the wrong call."""
    assert registered("garmin_workout_create").annotations.destructive_hint is False


def test_the_preview_tool_kept_its_read_only_annotation():
    """The reason the write lives in a second tool at all.

    `ToolAnnotations` are per tool, not per action, so one tool holding both
    would have to be annotated as a write -- and §5 warns that a client gating
    non-read-only tools behind a confirmation would then wall off the safest
    call in the flow. The split keeps both annotations honest.
    """
    annotations = registered("garmin_workouts").annotations

    assert annotations.read_only_hint is True
    assert annotations.idempotent_hint is True


def test_the_two_tools_are_both_registered_and_distinct():
    import asyncio

    names = {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert {"garmin_workouts", "garmin_workout_create"} <= names
    assert {"garmin_auth_status", "garmin_training", "garmin_health"} <= names
