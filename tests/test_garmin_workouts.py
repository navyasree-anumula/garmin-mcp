"""`garmin_workouts` end to end, and the fact that it reaches nothing (§5, v1.5).

The composer's own tests are in `test_compose.py`. This file covers the tool
layer: that a refusal arrives as a `ToolError` an agent can read rather than a
traceback, that the schema offers exactly the one action that exists, that the
annotations tell a client the truth, and that a preview makes **zero** Garmin
requests.

That last one is asserted by booby-trapping the doorway rather than by counting
calls. Counting proves nothing was called this time; a doorway that raises
proves there is no path to one at all.
"""

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp.server import WorkoutStep, garmin_workouts, mcp
from garmin_mcp.source import client as source
from garmin_mcp.source import compose as compose_module

EASY_RUN = [WorkoutStep(kind="interval", duration_secs=2400.0)]

SESSION = [
    WorkoutStep(kind="warmup", duration_secs=600.0),
    WorkoutStep(
        kind="repeat",
        iterations=4,
        repeat=[
            WorkoutStep(kind="interval", distance_m=800.0),
            WorkoutStep(kind="recovery", duration_secs=120.0),
        ],
    ),
    WorkoutStep(kind="cooldown", duration_secs=300.0),
]


@pytest.fixture(autouse=True)
def no_garmin_doorway(monkeypatch):
    """Every test in this file runs with the request doorway booby-trapped.

    Applied to the whole file rather than to one test, because "preview makes no
    requests" is a property of the tool and not of a single call. Any future
    test here that needs a request will fail loudly, which is the right default
    for a module whose entire claim is that it needs none.
    """

    def explode(*args, **kwargs):
        raise AssertionError("a preview must not open a Garmin request")

    monkeypatch.setattr(source, "_garmin_access", explode)
    monkeypatch.setattr(source, "_connect", explode)


def test_an_easy_run_previews():
    preview = garmin_workouts("preview", "Easy 40", EASY_RUN)

    assert preview.workout_name == "Easy 40"
    assert preview.sport == "running"
    assert preview.step_summary == ["interval: 2400s"]
    assert preview.estimated_duration_secs == 2400


def test_the_preview_reports_zero_requests():
    """A number, in the response, rather than a promise in a docstring. The
    agent can see what the call cost without being told."""
    assert garmin_workouts("preview", "Easy 40", EASY_RUN).requests_made == 0


def test_the_structure_is_the_one_the_composer_builds():
    """The tool must not compose its own variant. When `create` arrives it will
    take this structure back, so the thing previewed and the thing composed have
    to be the same object shape."""
    from garmin_mcp.server import _as_compose_steps

    preview = garmin_workouts("preview", "Thu threshold", SESSION)
    expected = compose_module.compose("Thu threshold", _as_compose_steps(SESSION))

    assert preview.structure == expected


def test_the_summary_indents_what_is_inside_a_repeat():
    """`structure` is 40 lines of Garmin's shape per step. A human approving a
    session should be able to read it without parsing `conditionTypeId`."""
    preview = garmin_workouts("preview", "Thu threshold", SESSION)

    assert preview.step_summary == [
        "warmup: 600s",
        "4x:",
        "    interval: 800m",
        "    recovery: 120s",
        "cooldown: 300s",
    ]


def test_the_estimate_arrives_with_its_basis():
    """The number is meaningless alone -- an all-distance session estimates 0 --
    so the two fields travel together and the basis explains the gap."""
    preview = garmin_workouts("preview", "Thu threshold", SESSION)

    assert preview.estimated_duration_secs == 1380
    assert "contribute 0" in preview.estimated_duration_basis


def test_the_note_says_nothing_was_sent_and_points_at_create():
    """Updated 2026-10-27, when `create` arrived. The note used to promise that
    approval *would* carry the structure; it now names the tool that does."""
    note = garmin_workouts("preview", "Easy 40", EASY_RUN).note

    assert "Nothing was sent to Garmin" in note
    assert "zero requests" in note
    assert "garmin_workout_create" in note
    assert "unchanged" in note, "re-composition is the failure the marker catches"


def test_the_preview_returns_an_approval_marker_for_its_structure():
    """The half deferred on 2026-10-26, now that it has a consumer.

    Asserted against the composer's own function rather than a literal digest: a
    hard-coded hash here would have to be edited every time the structure
    changes shape, and a test nobody can read the intent of gets edited without
    being understood.
    """
    from garmin_mcp.source import compose as compose_module

    preview = garmin_workouts("preview", "Thu threshold", SESSION)

    assert preview.approval.startswith("sha256:")
    assert preview.approval == compose_module.approval_for(preview.structure)


# --------------------------------------------------------------------------
# Refusals reach the agent as messages, not tracebacks
# --------------------------------------------------------------------------


def test_an_unknown_action_names_the_valid_ones():
    """The schema's enum stops a compliant client; this stops everything else.
    A "no" that does not say what would work costs the agent a round trip to
    find out -- the same reasoning as `_read_group`'s guard."""
    with pytest.raises(ToolError, match="Valid actions: preview"):
        garmin_workouts("create", "Easy 40", EASY_RUN)


def test_no_steps_is_refused():
    with pytest.raises(ToolError, match="not a session"):
        garmin_workouts("preview", "Easy 40", [])


def test_a_compose_refusal_arrives_as_a_tool_error():
    """`ComposeError` is a `GarminSourceError`, so the tool layer maps it the
    same way as every other source error -- the agent gets the sentence, not a
    stack trace."""
    bad = [WorkoutStep(kind="repeat", iterations=4, repeat=[])]

    with pytest.raises(ToolError, match="repeats nothing"):
        garmin_workouts("preview", "Bad", bad)


def test_a_step_measured_both_ways_is_refused_by_the_tool():
    bad = [WorkoutStep(kind="interval", duration_secs=60.0, distance_m=400.0)]

    with pytest.raises(ToolError, match="never both"):
        garmin_workouts("preview", "Bad", bad)


def test_an_empty_name_is_refused_by_the_tool():
    with pytest.raises(ToolError, match="needs a name"):
        garmin_workouts("preview", "   ", EASY_RUN)


def test_a_nested_repeat_is_refused_by_the_tool():
    inner = WorkoutStep(
        kind="repeat", iterations=2, repeat=[WorkoutStep(kind="interval", duration_secs=60.0)]
    )
    bad = [WorkoutStep(kind="repeat", iterations=3, repeat=[inner])]

    with pytest.raises(ToolError, match="nested"):
        garmin_workouts("preview", "Bad", bad)


# --------------------------------------------------------------------------
# What the schema tells a client
# --------------------------------------------------------------------------


def registered_tool(name: str):
    tools = asyncio.run(mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert name in by_name, f"{name} is not registered: {sorted(by_name)}"
    return by_name[name]


def test_the_annotations_are_honest():
    """A preview composes and returns and touches nothing, so it really is
    read-only and idempotent. It is **not** destructive, and it must not be
    marked so: a client that hides read-only tools behind a confirmation would
    put a wall in front of the safest call in the flow."""
    annotations = registered_tool("garmin_workouts").annotations

    assert annotations.read_only_hint is True
    assert annotations.idempotent_hint is True
    assert annotations.destructive_hint is False


def test_the_action_enum_offers_only_what_exists():
    """A schema that offered `create` before `create` was written would be a
    schema that lies to the agent -- it would call it, and get a refusal it
    could have been spared."""
    action = registered_tool("garmin_workouts").input_schema["properties"]["action"]

    # A single-value Literal serialises as `const`, not a one-item `enum`.
    assert action.get("const") == "preview"
    assert action.get("enum", ["preview"]) == ["preview"]


def test_the_step_kinds_in_the_schema_come_from_the_composer():
    """The enum is derived from `compose.STEP_KINDS`, not restated. A kind
    cannot appear in the schema without a builder behind it."""
    from garmin_mcp.server import StepKind
    from typing import get_args

    assert set(get_args(StepKind)) == set(compose_module.STEP_KINDS) | {"repeat"}


def test_the_read_tools_are_unaffected():
    """Adding a third tool must not disturb the two that work. The metric enums
    are built per group, so this is cheap to assert and catches a careless edit
    to `_metrics_help`."""
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert {"garmin_auth_status", "garmin_training", "garmin_health"} <= names
