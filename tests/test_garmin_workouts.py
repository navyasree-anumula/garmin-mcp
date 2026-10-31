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


# --------------------------------------------------------------------------
# Strength circuits at the tool layer (§5, v1.6 -- amendment not yet ratified)
#
# `test_compose.py` proves the structure. This proves the two things only the
# tool layer can get wrong: that the preview names the exercise Garmin will
# actually store rather than the word the agent typed, and that the trailing
# rest a repeat group cannot avoid is stated instead of discovered on the watch.
# --------------------------------------------------------------------------

CIRCUIT = [
    WorkoutStep(
        kind="repeat",
        iterations=3,
        repeat=[
            WorkoutStep(kind="exercise", duration_secs=40.0, exercise="goblet squat"),
            WorkoutStep(kind="rest", duration_secs=20.0),
            WorkoutStep(kind="exercise", duration_secs=40.0, exercise="push-up"),
            WorkoutStep(kind="rest", duration_secs=20.0),
        ],
    )
]


def test_a_strength_preview_reports_its_own_sport():
    """`sport` used to be the literal 'running'. A field that reports a
    constant is a field that lies the moment a second sport exists."""
    preview = garmin_workouts(
        action="preview", name="Thu circuit", steps=CIRCUIT, sport="strength"
    )

    assert preview.sport == "strength"
    assert preview.requests_made == 0


def test_the_summary_names_the_resolved_exercise_not_the_term_typed():
    """The human approves what Garmin will store. 'goblet squat' is the term;
    'Goblet Squat' and SQUAT/GOBLET_SQUAT are what lands, so the line carries
    all three parts of the answer and none of the question."""
    preview = garmin_workouts(
        action="preview", name="Thu circuit", steps=CIRCUIT, sport="strength"
    )

    assert preview.step_summary == [
        "3x:",
        "    exercise: Goblet Squat 40s [SQUAT/GOBLET_SQUAT]",
        "    rest: 20s",
        "    exercise: Push-up 40s [PUSH_UP/PUSH_UP]",
        "    rest: 20s",
    ]


def test_the_estimate_counts_every_round():
    preview = garmin_workouts(
        action="preview", name="Thu circuit", steps=CIRCUIT, sport="strength"
    )

    assert preview.estimated_duration_secs == 360


def test_a_circuit_ending_on_a_rest_says_so_in_the_note():
    """A repeat group cannot vary by iteration, so the final round keeps its
    rest. That is a property of the session being approved, and an approval is
    only informed if the thing is said out loud."""
    preview = garmin_workouts(
        action="preview", name="Thu circuit", steps=CIRCUIT, sport="strength"
    )

    assert "last round ends with a rest step" in preview.note


def test_a_circuit_ending_on_an_exercise_does_not_say_it():
    """The control. A note appended unconditionally would read as true here and
    would be false, which is worse than silence."""
    ends_on_work = [
        WorkoutStep(
            kind="repeat",
            iterations=3,
            repeat=[
                WorkoutStep(kind="rest", duration_secs=20.0),
                WorkoutStep(
                    kind="exercise", duration_secs=40.0, exercise="goblet squat"
                ),
            ],
        )
    ]

    preview = garmin_workouts(
        action="preview", name="Thu circuit", steps=ends_on_work, sport="strength"
    )

    assert "last round ends with a rest step" not in preview.note


def test_a_running_preview_never_grows_the_rest_note():
    """A running session has no rest steps at all, so the detector must not
    fire on a recovery step, which is a different thing wearing a similar name."""
    preview = garmin_workouts(action="preview", name="Thu threshold", steps=SESSION)

    assert "last round ends with a rest step" not in preview.note
    assert preview.sport == "running"


def test_an_ambiguous_exercise_arrives_as_a_tool_error():
    """A refusal the agent can read, not a traceback -- and it must carry the
    candidates, or the agent has nothing to retry with."""
    ambiguous = [
        WorkoutStep(kind="exercise", duration_secs=40.0, exercise="press")
    ]

    with pytest.raises(ToolError) as exc:
        garmin_workouts(
            action="preview", name="Bad", steps=ambiguous, sport="strength"
        )

    assert "matches 82 Garmin exercises" in str(exc.value)


def test_a_strength_kind_in_a_running_session_arrives_as_a_tool_error():
    """The schema's enum is the union of both sports' kinds, because a single
    Literal cannot be conditional. The refusal is what carries the real rule,
    so it has to survive the tool boundary."""
    with pytest.raises(ToolError, match="not a step kind for a running"):
        garmin_workouts(
            action="preview",
            name="Bad",
            steps=[
                WorkoutStep(kind="exercise", duration_secs=40.0, exercise="squat")
            ],
        )


def test_the_sports_in_the_schema_come_from_the_composer():
    """Same rule as the step kinds: a sport the schema offers and the composer
    cannot build is a schema that lies to the agent."""
    from typing import get_args

    from garmin_mcp.server import WorkoutSport

    assert set(get_args(WorkoutSport)) == set(compose_module.SPORTS)


def test_the_schema_offers_the_exercise_field():
    """An agent cannot pass a name the published schema does not mention, so
    the field's presence is part of the feature rather than a detail of it."""
    schema = registered_tool("garmin_workouts").input_schema
    step = schema["$defs"]["WorkoutStep"]["properties"]

    assert "exercise" in step
    assert "catalogue" in step["exercise"]["description"]


def test_a_strength_preview_still_reaches_nothing():
    """The doorway fixture is autouse, so this passes only if composition made
    no request. Strength resolution reads a module-level catalogue, and a
    catalogue that had turned into a lookup would fail here."""
    preview = garmin_workouts(
        action="preview", name="Thu circuit", steps=CIRCUIT, sport="strength"
    )

    assert preview.requests_made == 0
    assert preview.approval.startswith("sha256:")


# --------------------------------------------------------------------------
# An unknown field is named, not dropped (found live, 2026-10-31)
#
# A real session failed here. A client sent a repeat group's nested steps under
# `steps` instead of `repeat`; pydantic's default dropped the key, and the server
# refused with "a repeat group with no steps repeats nothing" -- blaming the
# group for being empty rather than naming the field nobody read. The agent then
# guessed field names for several turns and gave up. The schema was correct the
# whole time, which is the point: a correct schema does not help if a wrong guess
# produces a misdiagnosis.
# --------------------------------------------------------------------------


def call_raw(args: dict):
    """The tool through MCP with a raw dict, the way a client really calls it.

    The other tests here pass `WorkoutStep` objects, which cannot carry an
    unknown field -- so they could never have caught this. Going through
    `call_tool` is what makes the wire shape testable.
    """
    return asyncio.run(mcp.call_tool("garmin_workouts", args))


def test_an_unknown_field_on_a_step_is_named_not_dropped():
    """The exact live failure, as a regression test. The error must name the
    offending field: `steps.0.steps` is recoverable in one turn, while "the
    group has no steps" sent an agent guessing."""
    bad = {
        "action": "preview",
        "name": "probe",
        "sport": "strength",
        "steps": [
            {
                "kind": "repeat",
                "iterations": 4,
                "steps": [
                    {"kind": "exercise", "duration_secs": 40.0, "exercise": "squat"}
                ],
            }
        ],
    }

    with pytest.raises(ToolError) as exc:
        call_raw(bad)

    message = str(exc.value)
    assert "steps.0.steps" in message
    assert "Extra inputs are not permitted" in message
    # The misdiagnosis must NOT be what comes back any more.
    assert "repeat group with no steps" not in message


def test_the_control_the_right_field_name_composes():
    """A refusal-only test would pass against a tool that refused everything."""
    good = {
        "action": "preview",
        "name": "probe",
        "sport": "strength",
        "steps": [
            {
                "kind": "repeat",
                "iterations": 4,
                "repeat": [
                    {"kind": "exercise", "duration_secs": 40.0, "exercise": "squat"},
                    {"kind": "rest", "duration_secs": 20.0},
                ],
            }
        ],
    }

    assert call_raw(good) is not None


def test_an_unknown_field_at_the_top_of_a_step_is_refused_too():
    """Not only the nested case. A plausible-looking `reps` is the obvious thing
    an agent would try on a strength step, and v1.6 does not build reps -- so it
    has to be refused rather than silently ignored."""
    bad = {
        "action": "preview",
        "name": "probe",
        "sport": "strength",
        "steps": [
            {"kind": "exercise", "duration_secs": 40.0, "exercise": "squat", "reps": 12}
        ],
    }

    with pytest.raises(ToolError, match="Extra inputs are not permitted"):
        call_raw(bad)


def test_an_empty_repeat_group_names_the_field_it_wanted():
    """When the group really is empty, the refusal should still say where the
    steps go -- the message an agent reads is the only documentation it has at
    that moment."""
    bad = [WorkoutStep(kind="repeat", iterations=4, repeat=[])]

    with pytest.raises(ToolError) as exc:
        garmin_workouts(action="preview", name="probe", steps=bad, sport="strength")

    assert "`repeat` field" in str(exc.value)


def test_the_runner_circuit_from_the_live_session_composes():
    """The session that found the bug designed this and never got to send it.
    Six exercises, 40s work and 20s rest, four rounds -- 24 minutes. Pinned here
    so the shape a real session asked for stays buildable."""
    names = [
        "Goblet Squat",
        "Dumbbell Reverse Lunge",
        "Single-leg Romanian Deadlift with Dumbbell",
        "Hip Raise",
        "Calf Raise",
        "Plank",
    ]
    round_steps = []
    for name in names:
        round_steps.append(
            WorkoutStep(kind="exercise", duration_secs=40.0, exercise=name)
        )
        round_steps.append(WorkoutStep(kind="rest", duration_secs=20.0))

    preview = garmin_workouts(
        action="preview",
        name="Runner strength",
        sport="strength",
        steps=[WorkoutStep(kind="repeat", iterations=4, repeat=round_steps)],
    )

    assert preview.estimated_duration_secs == 1440
    assert preview.requests_made == 0
    assert "Goblet Squat" in preview.step_summary[1]
    assert "DUMBBELL_REVERSE_LUNGE" in preview.step_summary[3]


def test_the_documented_repeat_group_example_actually_works():
    """The example in the `steps` description, parsed and composed.

    Two live sessions were lost because the nested shape was invisible: it lives
    behind a recursive `$ref` and the client did not render one, so the agent
    guessed `steps` and was told the group was empty. The fix is prose, and
    prose rots -- so the exact string the schema publishes is parsed here and
    run through the tool. A documented example that no longer works reads as
    verified, which is worse than no example at all.
    """
    import json

    from garmin_mcp.server import REPEAT_GROUP_EXAMPLE

    steps = json.loads(REPEAT_GROUP_EXAMPLE)
    preview = garmin_workouts(
        action="preview",
        name="example",
        steps=[WorkoutStep(**s) for s in steps],
        sport="strength",
    )

    assert preview.estimated_duration_secs == 240
    assert "Goblet Squat" in preview.step_summary[1]


def test_the_example_is_in_the_published_description():
    """The constant is only useful if the schema actually carries it. Asserted
    against the published schema, not the source, because the schema is what
    the agent reads."""
    from garmin_mcp.server import REPEAT_GROUP_EXAMPLE

    described = registered_tool("garmin_workouts").input_schema["properties"]["steps"][
        "description"
    ]

    assert REPEAT_GROUP_EXAMPLE in described
    assert "NOT `steps`" in described


def test_the_example_survives_the_raw_wire_path():
    """The same example as a client would send it -- a raw dict through
    `call_tool`, which is the path that found the original bug."""
    import json

    from garmin_mcp.server import REPEAT_GROUP_EXAMPLE

    assert call_raw({
        "action": "preview",
        "name": "example",
        "sport": "strength",
        "steps": json.loads(REPEAT_GROUP_EXAMPLE),
    }) is not None


def test_a_strength_preview_renders_a_warmup_and_cooldown():
    """The friction the first live session hit: it was told to drop the timed
    warm-up and cool-down and warm up unaided. A session that can hold them is
    only useful if the preview shows them, since the preview is what gets
    approved."""
    preview = garmin_workouts(
        action="preview",
        name="Thu session",
        sport="strength",
        steps=[
            WorkoutStep(kind="warmup", duration_secs=300.0),
            WorkoutStep(
                kind="repeat",
                iterations=3,
                repeat=[
                    WorkoutStep(
                        kind="exercise", duration_secs=40.0, exercise="goblet squat"
                    ),
                    WorkoutStep(kind="rest", duration_secs=20.0),
                ],
            ),
            WorkoutStep(kind="cooldown", duration_secs=180.0),
        ],
    )

    assert preview.step_summary == [
        "warmup: 300s",
        "3x:",
        "    exercise: Goblet Squat 40s [SQUAT/GOBLET_SQUAT]",
        "    rest: 20s",
        "cooldown: 180s",
    ]
    assert preview.estimated_duration_secs == 660
    assert preview.requests_made == 0


def test_a_running_kind_in_strength_still_refuses_at_the_tool():
    """`warmup` crossing over must not have brought `recovery` and `interval`
    with it. The per-sport rule is the only thing standing between a flat enum
    and a session Garmin would reject."""
    with pytest.raises(ToolError, match="not a step kind for a strength"):
        garmin_workouts(
            action="preview",
            name="Bad",
            sport="strength",
            steps=[WorkoutStep(kind="recovery", duration_secs=120.0)],
        )


def test_the_exercise_field_points_at_the_search_tool():
    """A refusal is only actionable if the schema says what to do about it.
    The composer cannot name an MCP tool -- source/ does not know MCP exists --
    so the pointer lives here, which is also where an agent reads it."""
    schema = registered_tool("garmin_workouts").input_schema
    described = schema["$defs"]["WorkoutStep"]["properties"]["exercise"]["description"]

    assert "garmin_exercises" in described


# --------------------------------------------------------------------------
# Pace targets at the tool layer (§5/§8, v1.6). The composer's own pace tests
# are in `test_compose.py`; what matters here is that the words a human
# approves and the bytes Garmin would store are two renderings of one value.
# --------------------------------------------------------------------------


PACED_SESSION = [
    WorkoutStep(kind="warmup", duration_secs=600.0),
    WorkoutStep(
        kind="interval", distance_m=5000.0, pace_min_km=["4:20", "4:40"]
    ),
    WorkoutStep(kind="cooldown", duration_secs=300.0),
]


def test_the_preview_shows_the_pace_in_min_per_km():
    """A human approves min/km and must never be shown m/s. The structure
    carries 3.846 and the summary says 4:20 -- same value, two audiences."""
    preview = garmin_workouts(action="preview", name="Tempo 5k", steps=PACED_SESSION)

    assert preview.step_summary == [
        "warmup: 600s",
        "interval: 5000m @ 4:20-4:40/km",
        "cooldown: 300s",
    ]
    assert preview.requests_made == 0


def test_the_summary_and_the_structure_cannot_disagree():
    """**The §8 requirement this whole feature turns on** (Pass A.1, B.3). One
    conversion, two readers. If the summary re-parsed the argument while the
    structure used the converted value, they could drift -- and the human would
    approve the right words over the wrong workout. Asserted by converting the
    words in the summary back and finding the numbers in the payload."""
    preview = garmin_workouts(action="preview", name="Tempo 5k", steps=PACED_SESSION)

    line = next(line for line in preview.step_summary if "@" in line)
    shown = line.split("@")[1].strip().removesuffix("/km")
    faster_shown, slower_shown = shown.split("-")

    contract = compose_module.PACE_CONTRACT
    step = next(
        item
        for item in preview.structure["workoutSegments"][0]["workoutSteps"]
        if (item.get("targetType") or {}).get("workoutTargetTypeKey")
        == contract.target_key
    )

    expected = compose_module.pace_target([faster_shown, slower_shown])
    assert step[contract.faster_bound_field] == expected.faster_m_s
    assert step[contract.slower_bound_field] == expected.slower_m_s


def test_the_approval_marker_covers_the_pace():
    """A marker that did not change with the pace would let a human approve a
    session at one pace and a create carry another."""
    paced = garmin_workouts(action="preview", name="Tempo 5k", steps=PACED_SESSION)
    unpaced = garmin_workouts(
        action="preview",
        name="Tempo 5k",
        steps=[
            WorkoutStep(kind="warmup", duration_secs=600.0),
            WorkoutStep(kind="interval", distance_m=5000.0),
            WorkoutStep(kind="cooldown", duration_secs=300.0),
        ],
    )

    assert paced.approval != unpaced.approval


def test_the_basis_in_the_preview_stops_claiming_there_is_no_pace():
    """The basis is read by the human, not by a test of `estimate_duration`,
    so the sentence is asserted where it is actually surfaced."""
    preview = garmin_workouts(action="preview", name="Tempo 5k", steps=PACED_SESSION)

    assert "this account has no measured pace" not in preview.estimated_duration_basis
    assert "prescription" in preview.estimated_duration_basis
    assert preview.estimated_duration_secs == 900


def test_a_pace_in_a_strength_session_refuses_at_the_tool():
    """The refusal has to arrive as a `ToolError` an agent can read, not as a
    traceback -- and it must name the reason, because the agent's next move is
    to drop the pace rather than to guess another field."""
    with pytest.raises(ToolError, match="pace target is for a running step"):
        garmin_workouts(
            action="preview",
            name="Circuit",
            sport="strength",
            steps=[
                WorkoutStep(kind="warmup", duration_secs=300.0, pace_min_km=["4:20", "4:40"]),
                WorkoutStep(kind="exercise", duration_secs=40.0, exercise="goblet squat"),
                WorkoutStep(kind="rest", duration_secs=20.0),
            ],
        )


def test_the_same_strength_session_previews_without_the_pace():
    """The control. The refusal above must be about the pace and not about the
    session."""
    preview = garmin_workouts(
        action="preview",
        name="Circuit",
        sport="strength",
        steps=[
            WorkoutStep(kind="warmup", duration_secs=300.0),
            WorkoutStep(kind="exercise", duration_secs=40.0, exercise="goblet squat"),
            WorkoutStep(kind="rest", duration_secs=20.0),
        ],
    )

    assert preview.step_summary == [
        "warmup: 300s",
        "exercise: Goblet Squat 40s [SQUAT/GOBLET_SQUAT]",
        "rest: 20s",
    ]


def test_a_malformed_pace_refuses_at_the_tool_with_the_reason():
    with pytest.raises(ToolError, match="seconds must be under 60"):
        garmin_workouts(
            action="preview",
            name="Bad",
            steps=[
                WorkoutStep(kind="interval", distance_m=1000.0, pace_min_km=["4:75", "5:00"])
            ],
        )


def test_the_pace_field_is_published_with_its_rules():
    """`extra="forbid"` means an undeclared field is refused, so the schema
    field and the composer had to ship together (§15 3.c / A.6). This asserts
    the field is actually published AND that its description carries the shape
    -- a declared field nobody can see the format of is unreachable in
    practice."""
    schema = registered_tool("garmin_workouts").input_schema
    described = schema["$defs"]["WorkoutStep"]["properties"]["pace_min_km"][
        "description"
    ]

    assert "min/km" in described
    assert "faster bound first" in described
    assert "4:20" in described


def test_an_undeclared_target_field_is_still_refused():
    """The pace field is the one target input that exists. A client guessing
    `heart_rate_zone` must be told the field is wrong rather than having it
    dropped, which is the failure that cost a live session on 2026-10-31."""
    with pytest.raises(Exception, match="heart_rate_zone"):
        WorkoutStep(kind="interval", duration_secs=600.0, heart_rate_zone=3)
