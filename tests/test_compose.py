"""Composition and refusal, with no network anywhere (§5/§8, v1.5).

This is the half of the write path that can be tested properly, which is why it
ships first. Everything here is a pure function over `garminconnect.workout`'s
builders: no tokens, no budget, no lock, no 429, no Garmin.

Two kinds of test, and the second kind is the one that matters. Composing a
session proves we can build a structure; **refusing a malformed one proves we
will not hand Garmin something it should reject.** Every refusal is paired with
the valid variant passing, because a validator only ever seen to refuse might be
refusing everything.

The hand-mutated structures below are deliberate. Four of §8's cases are
unreachable through `compose` -- it assigns `stepOrder` itself and passes no
target -- so they are reached by composing a good session and breaking it. That
is also the exact path `create` will take when it validates a structure that
came back from a conversation rather than from the composer.
"""

import ast
import copy
import json
import math
from pathlib import Path

import pytest

from garmin_mcp.source import compose
from garmin_mcp.source.compose import ComposeError, Step

EASY_RUN = (Step("interval", duration_secs=2400.0),)

SESSION = (
    Step("warmup", duration_secs=600.0),
    Step(
        "repeat",
        iterations=4,
        repeat=(
            Step("interval", distance_m=800.0),
            Step("recovery", duration_secs=120.0),
        ),
    ),
    Step("cooldown", duration_secs=300.0),
)


def steps_of(structure):
    return structure["workoutSegments"][0]["workoutSteps"]


def flat_orders(structure):
    return [item["stepOrder"] for item, _ in compose._flatten(steps_of(structure))]


# --------------------------------------------------------------------------
# What a composed session looks like
# --------------------------------------------------------------------------


def test_an_easy_run_composes_to_one_step():
    structure = compose.compose("Easy 40", EASY_RUN)

    assert structure["workoutName"] == "Easy 40"
    assert structure["sportType"]["sportTypeKey"] == "running"
    assert len(steps_of(structure)) == 1
    assert steps_of(structure)[0]["endConditionValue"] == 2400.0


def test_a_repeat_session_numbers_its_steps_contiguously():
    """`stepOrder` is flat across the segment, not per-group.

    Garmin orders a segment by this field, so the repeat's children share the
    parent's numbering rather than restarting at 1. Getting this wrong produces
    a workout whose steps run in an undefined order -- which is why the
    composer owns the counter and the caller cannot supply it.
    """
    structure = compose.compose("Thu threshold", SESSION)

    # warmup 1, repeat 2, its two children 3 and 4, cooldown 5.
    assert flat_orders(structure) == [1, 2, 3, 4, 5]


def test_every_step_carries_an_explicit_no_target():
    """v0's whole target story rests on the builders' default, so assert it.

    `create_warmup_step(..., target_type=None)` does not leave `targetType`
    absent -- it fills in NO_TARGET. Read from the library source rather than
    assumed, and asserted here because if upstream ever changed it to `None`,
    `exclude_none` would drop the field and we would be uploading steps with no
    target field at all.
    """
    structure = compose.compose("Thu threshold", SESSION)

    for item, _ in compose._flatten(steps_of(structure)):
        if item["type"] == "RepeatGroupDTO":
            continue
        assert item["targetType"]["workoutTargetTypeKey"] == compose.NO_TARGET_KEY
        assert item["targetType"]["workoutTargetTypeId"] == compose.NO_TARGET_ID


def test_the_repeat_group_carries_its_iteration_count_in_both_fields():
    """`create_repeat_group` writes the count twice. Two fields, one fact.

    Asserted so that a future divergence is a test failure here rather than a
    workout that repeats a different number of times than it says.
    """
    group = steps_of(compose.compose("Thu threshold", SESSION))[1]

    assert group["numberOfIterations"] == 4
    assert float(group["endConditionValue"]) == 4.0


def test_the_name_is_stripped_and_the_description_rides_along():
    structure = compose.compose("  Thu threshold  ", EASY_RUN, "week 3")

    assert structure["workoutName"] == "Thu threshold"
    assert structure["description"] == "week 3"


def test_no_description_means_the_field_is_absent_not_null():
    """`to_dict` uses `exclude_none`, so an absent description must not arrive
    as an explicit null -- Garmin's acceptance of a null there is unmeasured."""
    assert "description" not in compose.compose("Easy 40", EASY_RUN)


# --------------------------------------------------------------------------
# What we refuse, and the valid variant of each
# --------------------------------------------------------------------------


def test_v1_a_workout_with_no_steps_is_refused():
    with pytest.raises(ComposeError, match="no steps"):
        compose.compose("Empty", ())


def test_v1_control_one_step_is_enough():
    assert steps_of(compose.compose("Easy 40", EASY_RUN))


def test_v2_a_repeat_group_with_no_steps_is_refused():
    with pytest.raises(ComposeError, match="repeats nothing"):
        compose.compose("Bad", (Step("repeat", iterations=4),))


def test_v2_control_a_repeat_group_with_a_step_is_fine():
    assert compose.compose(
        "Fine", (Step("repeat", iterations=4, repeat=(Step("interval", duration_secs=60.0),)),)
    )


@pytest.mark.parametrize("iterations", [0, 1, -3])
def test_v3_fewer_than_two_iterations_is_refused(iterations):
    """Zero reps is §8's case. One is ours: a group that runs once is not a
    repeat, and silently accepting it hides a prescription bug behind a
    structure that is technically valid."""
    with pytest.raises(ComposeError, match="at least 2 iterations"):
        compose.compose(
            "Bad",
            (Step("repeat", iterations=iterations, repeat=(Step("interval", duration_secs=60.0),)),),
        )


def test_v3_missing_iterations_is_refused_separately():
    with pytest.raises(ComposeError, match="needs iterations"):
        compose.compose(
            "Bad", (Step("repeat", repeat=(Step("interval", duration_secs=60.0),)),)
        )


def test_v3_control_two_iterations_is_the_minimum_that_passes():
    assert compose.compose(
        "Fine", (Step("repeat", iterations=2, repeat=(Step("interval", duration_secs=60.0),)),)
    )


def test_v4_a_duplicated_step_order_is_refused():
    """Unreachable through `compose`; reachable through a structure that came
    from somewhere else, which is what `create` will be handed."""
    structure = compose.compose("Thu threshold", SESSION)
    steps_of(structure)[0]["stepOrder"] = steps_of(structure)[1]["stepOrder"]

    with pytest.raises(ComposeError, match="duplicated"):
        compose.validate_structure(structure)


def test_v4_a_non_contiguous_step_order_is_refused():
    structure = compose.compose("Easy 40", EASY_RUN)
    steps_of(structure)[0]["stepOrder"] = 7

    with pytest.raises(ComposeError, match="contiguous"):
        compose.validate_structure(structure)


def test_v4_control_the_composer_always_produces_contiguous_orders():
    compose.validate_structure(compose.compose("Thu threshold", SESSION))


@pytest.mark.parametrize("bad", [0, -60.0, math.nan, math.inf, -math.inf])
def test_v5_a_non_positive_or_non_finite_duration_is_refused(bad):
    """`float` accepts NaN and infinity and typing does not catch either.

    `endConditionValue` is a float all the way to the wire, so a NaN duration
    would serialise and travel. Refused where the number is known to be a
    measure.
    """
    with pytest.raises(ComposeError, match="finite|greater than zero"):
        compose.compose("Bad", (Step("interval", duration_secs=bad),))


@pytest.mark.parametrize("bad", [0, -800.0, math.nan])
def test_v5_distance_is_checked_the_same_way(bad):
    with pytest.raises(ComposeError, match="finite|greater than zero"):
        compose.compose("Bad", (Step("interval", distance_m=bad),))


def test_v5_control_a_small_positive_duration_passes():
    assert compose.compose("Fine", (Step("interval", duration_secs=1.0),))


def test_v6_a_target_other_than_no_target_is_refused():
    """The reason is in the message: every TargetType Garmin offers is
    zone-based and this account's zones have never been fetched."""
    structure = compose.compose("Easy 40", EASY_RUN)
    steps_of(structure)[0]["targetType"] = {
        "workoutTargetTypeId": 4,
        "workoutTargetTypeKey": "heart.rate.zone",
        "displayOrder": 4,
    }

    with pytest.raises(ComposeError, match="no targets"):
        compose.validate_structure(structure)


def test_v6_a_missing_target_is_refused_too():
    """Not the same case as a wrong target, and worth its own assertion: an
    absent `targetType` is what an upstream change to the builders' default
    would produce."""
    structure = compose.compose("Easy 40", EASY_RUN)
    del steps_of(structure)[0]["targetType"]

    with pytest.raises(ComposeError, match="no targets"):
        compose.validate_structure(structure)


@pytest.mark.parametrize("name", ["", "   "])
def test_v7_an_empty_name_is_refused(name):
    """Only the empty name. Garmin's length limit has never been measured, so
    no cap is invented here -- an invented limit refuses sessions Garmin would
    have accepted."""
    with pytest.raises(ComposeError, match="needs a name"):
        compose.compose(name, EASY_RUN)


def test_v7_control_a_long_name_is_not_refused():
    assert compose.compose("x" * 200, EASY_RUN)


def test_v8_a_repeat_inside_a_repeat_is_refused():
    inner = Step("repeat", iterations=2, repeat=(Step("interval", duration_secs=60.0),))

    with pytest.raises(ComposeError, match="nested"):
        compose.compose("Bad", (Step("repeat", iterations=3, repeat=(inner,)),))


def test_v8_the_structure_check_catches_nesting_too():
    """The composer refuses to build one; the validator refuses to bless one
    that arrived already built."""
    structure = compose.compose("Thu threshold", SESSION)
    group = copy.deepcopy(steps_of(structure)[1])
    group["stepOrder"] = 3
    steps_of(structure)[1]["workoutSteps"] = [group]

    with pytest.raises(ComposeError, match="nested|duplicated|contiguous"):
        compose.validate_structure(structure)


def test_v9_disagreeing_iteration_fields_are_refused():
    structure = compose.compose("Thu threshold", SESSION)
    steps_of(structure)[1]["endConditionValue"] = 9.0

    with pytest.raises(ComposeError, match="disagree"):
        compose.validate_structure(structure)


def test_a_step_measured_both_ways_is_refused():
    with pytest.raises(ComposeError, match="never both"):
        compose.compose(
            "Bad", (Step("interval", duration_secs=60.0, distance_m=400.0),)
        )


def test_a_step_measured_no_way_is_refused():
    with pytest.raises(ComposeError, match="needs an end condition"):
        compose.compose("Bad", (Step("interval"),))


def test_only_an_interval_may_be_measured_in_distance():
    """A warmup by distance is expressible upstream and left out of v0: every
    kind offered is a kind the live cycle has to confirm."""
    with pytest.raises(ComposeError, match="only an interval"):
        compose.compose("Bad", (Step("warmup", distance_m=1000.0),))


def test_an_unknown_step_kind_names_the_valid_ones():
    with pytest.raises(ComposeError, match="Valid kinds"):
        compose.compose("Bad", (Step("sprint", duration_secs=30.0),))


def test_a_structure_with_no_segments_is_refused():
    with pytest.raises(ComposeError, match="workoutSegments"):
        compose.validate_structure({"workoutName": "x"})


def test_a_structure_with_no_name_is_refused():
    with pytest.raises(ComposeError, match="no workoutName"):
        compose.validate_structure({"workoutName": "  ", "workoutSegments": [{}]})


def test_a_step_with_no_end_condition_value_is_refused():
    structure = compose.compose("Easy 40", EASY_RUN)
    del steps_of(structure)[0]["endConditionValue"]

    with pytest.raises(ComposeError, match="nothing ends it"):
        compose.validate_structure(structure)


# --------------------------------------------------------------------------
# The estimate, and what it admits to leaving out
# --------------------------------------------------------------------------


def test_an_all_time_session_estimates_the_sum():
    seconds, basis = compose.estimate_duration(
        (Step("warmup", duration_secs=600.0), Step("interval", duration_secs=1800.0))
    )

    assert seconds == 2400
    assert "time-based" in basis


def test_an_all_distance_session_estimates_zero_and_says_why():
    """A bare 0 reads as broken, so the number is never returned without its
    basis -- and the basis has to explain the 0, not just describe the method."""
    seconds, basis = compose.estimate_duration((Step("interval", distance_m=10000.0),))

    assert seconds == 0
    assert "contribute 0" in basis
    assert "no measured pace" in basis
    assert "unverified" in basis


def test_a_repeat_multiplies_its_time_by_its_iterations():
    seconds, _ = compose.estimate_duration(SESSION)

    # 600 warmup + 4 x 120 recovery + 300 cooldown. The 4 x 800m contributes 0.
    assert seconds == 1380


def test_the_distance_count_in_the_basis_counts_every_iteration():
    """Four 800m reps are four steps that contributed nothing, not one. A count
    that ignored the repeat would under-report what the estimate is missing."""
    _, basis = compose.estimate_duration(SESSION)

    assert "4 distance step(s)" in basis


def test_the_estimate_lands_in_the_structure():
    assert compose.compose("Thu threshold", SESSION)["estimatedDurationInSecs"] == 1380


# --------------------------------------------------------------------------
# The approval marker, and the transport it has to survive
# --------------------------------------------------------------------------


def through_a_javascript_client(value):
    """What a JS client does to our numbers, and nothing else.

    JavaScript has one number type, so `JSON.stringify(600.0)` emits `600`.
    Python's own `json` round trip preserves `600.0`, which is why every test in
    this repo passed while the real handoff was broken: the structures were
    passed in-process as objects and the transport was never exercised.

    Written as a helper rather than a `json.dumps`/`loads` pair precisely
    because the Python pair does NOT reproduce the bug.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: through_a_javascript_client(v) for key, v in value.items()}
    if isinstance(value, list):
        return [through_a_javascript_client(v) for v in value]
    return value


def test_the_marker_survives_a_javascript_client():
    """The 2026-10-27 bug. Claude Desktop refused its own preview: the structure
    was numerically identical and textually different, so every workout with a
    whole-number duration -- which is nearly all of them -- was unwritable."""
    structure = compose.compose("throwaway test", SESSION)

    assert compose.approval_for(
        through_a_javascript_client(structure)
    ) == compose.approval_for(structure)


def test_the_marker_is_stable_across_calls():
    structure = compose.compose("Thu threshold", SESSION)

    assert compose.approval_for(structure) == compose.approval_for(structure)


def test_the_marker_ignores_key_order():
    """A client that round-trips through its own serialiser may reorder keys it
    never looked at. That must not invalidate an approval."""
    structure = compose.compose("Thu threshold", SESSION)
    reordered = json.loads(json.dumps(structure, sort_keys=True))

    assert compose.approval_for(reordered) == compose.approval_for(structure)


@pytest.mark.parametrize(
    "edit",
    [
        {"workoutName": "something else"},
        {"estimatedDurationInSecs": 99},
        {"description": "snuck in"},
    ],
    ids=["name", "duration", "description"],
)
def test_an_edited_structure_gets_a_different_marker(edit):
    """The failure direction that matters. Too strict costs a round trip; too
    loose puts a session the human did not approve on their watch."""
    structure = compose.compose("Thu threshold", SESSION)
    edited = dict(structure) | edit

    assert compose.approval_for(edited) != compose.approval_for(structure)


def test_an_edited_step_deep_in_the_tree_gets_a_different_marker():
    """The edit that would matter most and is hardest to see: one rep's
    distance, four levels down inside a repeat group."""
    structure = compose.compose("Thu threshold", SESSION)
    edited = json.loads(json.dumps(structure))
    group = edited["workoutSegments"][0]["workoutSteps"][1]
    group["workoutSteps"][0]["endConditionValue"] = 1200.0

    assert compose.approval_for(edited) != compose.approval_for(structure)


def test_a_fractional_value_is_not_rounded_into_a_collision():
    """Normalisation must only collapse float/int spellings of the SAME number.
    If 600.5 and 600 hashed alike, a half-minute edit would pass as approved."""
    whole = compose.compose("x", (Step("interval", duration_secs=600.0),))
    fraction = compose.compose("x", (Step("interval", duration_secs=600.5),))

    assert compose.approval_for(whole) != compose.approval_for(fraction)


def test_a_boolean_is_not_normalised_into_a_number():
    """`True` is an `int` subclass, so a careless normaliser turns it into 1 --
    and then `{"displayable": true}` and `{"displayable": 1}` would hash alike
    while meaning different things to whoever reads the payload."""
    assert compose.approval_for({"flag": True}) != compose.approval_for({"flag": 1})


def test_two_different_sessions_do_not_share_a_marker():
    """The stale-structure case: two previews in one conversation and the wrong
    pair sent on."""
    assert compose.approval_for(
        compose.compose("Easy 40", EASY_RUN)
    ) != compose.approval_for(compose.compose("Thu threshold", SESSION))


# --------------------------------------------------------------------------
# The claim that this module makes no requests
# --------------------------------------------------------------------------

MODULE = Path(compose.__file__)


def names_used(path: Path) -> set[str]:
    """Every name and attribute mentioned, plus every module imported.

    Walks the AST rather than grepping, in the shape of
    `tests/test_fetch_seam.py`'s doorway check: an import inside a function
    would pass a grep for a top-level import line.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_compose_never_reaches_the_request_doorway():
    """"Makes no requests" is a claim that rots the first time somebody adds a
    convenient lookup. `_garmin_access` is the only way to a Garmin request, so
    its absence is the structural form of the claim."""
    used = names_used(MODULE)

    assert "_garmin_access" not in used
    assert not {name for name in used if name.endswith("client")}


def test_the_doorway_check_can_actually_fail(tmp_path):
    """A check that has never been seen to fire is decoration."""
    offender = tmp_path / "offender.py"
    offender.write_text(
        "def f():\n"
        "    from .client import _garmin_access\n"
        "    return _garmin_access\n"
    )

    assert "_garmin_access" in names_used(offender)


def test_compose_does_import_the_builders():
    """The mirror image: if this module imported nothing at all, the check
    above would pass for the wrong reason."""
    assert "garminconnect.workout" in names_used(MODULE)


def test_composing_works_with_the_doorway_booby_trapped(monkeypatch):
    """The runtime half of the same claim. If anything in composition reached
    Garmin, this would raise instead of returning a structure."""
    from garmin_mcp.source import client as source

    def explode(*args, **kwargs):
        raise AssertionError("composition must not open a Garmin request")

    monkeypatch.setattr(source, "_garmin_access", explode)

    assert compose.compose("Thu threshold", SESSION)["workoutName"] == "Thu threshold"


# --------------------------------------------------------------------------
# Strength circuits (§5, v1.6 -- amendment drafted, not yet ratified)
#
# The shape this section exercises is the one the library does not build: a
# named exercise measured in TIME. `create_strength_exercise_step` ends on reps
# and `create_strength_rest_step` names nothing, so `_build_exercise` combines
# them. Garmin's acceptance of that combination was the open question of v1.6
# and a live create answered it YES on 2026-10-31 (workout 1746708075, scheduled
# and visible on the watch). These tests still only prove the structure is what
# we meant to send -- the live create is what proves Garmin wants it, and that
# evidence lives in the journal, not here.
# --------------------------------------------------------------------------

CIRCUIT = (
    Step(
        "repeat",
        iterations=3,
        repeat=(
            Step("exercise", duration_secs=40.0, exercise="goblet squat"),
            Step("rest", duration_secs=20.0),
            Step("exercise", duration_secs=40.0, exercise="push-up"),
            Step("rest", duration_secs=20.0),
        ),
    ),
)


def work_steps(structure):
    """Every strength work step, in order. Rests and the group are skipped."""
    return [
        item
        for item, _ in compose._flatten(steps_of(structure))
        if (item.get("stepType") or {}).get("stepTypeKey") == "interval"
    ]


def test_a_strength_circuit_carries_the_strength_sport_type_in_both_places():
    """The segment repeats the workout's sport type. Two copies of one fact is
    two things that can disagree, so both come from `_sport_type`."""
    structure = compose.compose("Thu circuit", CIRCUIT, sport="strength")

    expected = {
        "sportTypeId": 5,
        "sportTypeKey": "strength_training",
        "displayOrder": 5,
    }
    assert structure["sportType"] == expected
    assert structure["workoutSegments"][0]["sportType"] == expected


def test_the_sport_type_block_is_read_off_the_library_model():
    """Asserted against the library rather than against a literal of ours: if
    upstream renumbers a sport, this fails instead of silently disagreeing."""
    from garminconnect.workout import StrengthWorkout

    structure = compose.compose("Thu circuit", CIRCUIT, sport="strength")
    upstream = StrengthWorkout.model_fields["sportType"].default_factory()

    assert structure["sportType"] == upstream


def test_a_timed_exercise_step_carries_the_catalogue_pair():
    """Garmin stores an exercise as a category and a name from its own
    catalogue. A step carrying neither is an unnamed block on the watch."""
    structure = compose.compose("Thu circuit", CIRCUIT, sport="strength")
    first, second = work_steps(structure)

    assert (first["category"], first["exerciseName"]) == ("SQUAT", "GOBLET_SQUAT")
    assert (second["category"], second["exerciseName"]) == ("PUSH_UP", "PUSH_UP")


def test_the_exercise_step_ends_on_time_not_reps():
    """The whole point of v1.6. `create_strength_exercise_step` would put REPS
    (id 10) here with the rep count as the value; this asserts TIME (id 2) with
    the seconds, because a step that silently became 40 REPS of an exercise is
    the failure this literal exists to catch."""
    structure = compose.compose("Thu circuit", CIRCUIT, sport="strength")
    first = work_steps(structure)[0]

    assert first["endCondition"]["conditionTypeId"] == 2
    assert first["endCondition"]["conditionTypeKey"] == "time"
    assert first["endConditionValue"] == 40.0
    assert first["stepType"]["stepTypeKey"] == "interval"


def test_a_strength_rest_step_is_timed_and_names_nothing():
    """The rest is upstream's builder, unmodified. It carries no category by
    design, which is why `validate_structure` exempts it."""
    structure = compose.compose("Thu circuit", CIRCUIT, sport="strength")
    rests = [
        item
        for item, _ in compose._flatten(steps_of(structure))
        if (item.get("stepType") or {}).get("stepTypeKey") == "rest"
    ]

    assert len(rests) == 2
    for rest in rests:
        assert rest["endCondition"]["conditionTypeKey"] == "time"
        assert rest["endConditionValue"] == 20.0
        assert "category" not in rest


def test_a_strength_circuit_numbers_its_steps_contiguously():
    """Garmin's `stepOrder` is flat across the segment, not per-group. Five
    steps: the group, then its four children."""
    structure = compose.compose("Thu circuit", CIRCUIT, sport="strength")

    assert flat_orders(structure) == [1, 2, 3, 4, 5]


def test_a_strength_circuit_estimates_time_times_rounds():
    """3 x (40 + 20 + 40 + 20)."""
    estimated, basis = compose.estimate_duration(CIRCUIT)

    assert estimated == 360
    assert "time-based" in basis


def test_every_strength_step_carries_an_explicit_no_target():
    """v0's target story holds in v1.6: the account's zones have still never
    been fetched, so a strength step guesses nothing either."""
    structure = compose.compose("Thu circuit", CIRCUIT, sport="strength")

    for item, _ in compose._flatten(steps_of(structure)):
        if item.get("type") == "RepeatGroupDTO":
            continue
        assert item["targetType"]["workoutTargetTypeKey"] == compose.NO_TARGET_KEY
        assert item["targetType"]["workoutTargetTypeId"] == compose.NO_TARGET_ID


# -------------------------- exercise resolution ---------------------------


def test_an_exact_name_beats_its_own_substring_matches():
    """'Squat' is the display name of one exercise AND a substring of 125
    others. Substring-first would refuse the name a human is most likely to
    type, so the exact pass runs first. This is the ordering, asserted."""
    assert len(compose.EXERCISES) > 1000
    assert len([e for e in compose.EXERCISES if "squat" in e["name"].lower()]) > 100

    assert compose.resolve_exercise("Squat")["exercise"] == "SQUAT"


def test_resolution_is_case_insensitive():
    """Making a human match Garmin's capitalisation is a refusal that teaches
    nothing."""
    assert compose.resolve_exercise("goblet squat")["exercise"] == "GOBLET_SQUAT"
    assert compose.resolve_exercise("GOBLET SQUAT")["exercise"] == "GOBLET_SQUAT"


def test_a_unique_substring_resolves():
    """The second pass earns its place: 'zercher' names no exercise exactly."""
    assert compose.resolve_exercise("zercher squat")["exercise"] == "ZERCHER_SQUAT"


def test_an_ambiguous_exercise_is_refused_with_its_candidates():
    """Refusing costs one turn; guessing puts a different exercise on the watch
    under a name that reads correct in the preview. The candidate list is what
    makes the refusal actionable rather than a wall."""
    with pytest.raises(ComposeError) as exc:
        compose.resolve_exercise("press")

    message = str(exc.value)
    assert "matches 82 Garmin exercises" in message
    assert "and 74 more" in message
    assert message.count("'") >= 16  # the eight candidates, quoted


def test_an_unknown_exercise_is_refused():
    with pytest.raises(ComposeError, match="no Garmin exercise is named"):
        compose.resolve_exercise("nonsense exercise xyz")


@pytest.mark.parametrize("name", [None, "", "   "])
def test_an_exercise_step_with_no_name_is_refused(name):
    with pytest.raises(ComposeError, match="needs an exercise name"):
        compose.resolve_exercise(name)


# ---------------------------- the refusals --------------------------------


def test_an_exercise_name_on_a_non_exercise_step_is_refused():
    """Garmin would drop it and the watch would show a nameless step. A silent
    drop is the failure direction this project refuses."""
    bad = (
        Step(
            "repeat",
            iterations=2,
            repeat=(
                Step("exercise", duration_secs=40.0, exercise="squat"),
                Step("rest", duration_secs=20.0, exercise="goblet squat"),
            ),
        ),
    )

    with pytest.raises(ComposeError, match="carries an exercise name"):
        compose.compose("Bad", bad, sport="strength")


def test_a_running_kind_in_a_strength_session_is_refused():
    """'interval' means run hard for 400m and has no meaning in a circuit."""
    bad = (Step("interval", duration_secs=400.0),)

    with pytest.raises(ComposeError, match="not a step kind for a strength"):
        compose.compose("Bad", bad, sport="strength")


def test_a_strength_kind_in_a_running_session_is_refused():
    """The mirror image, and the reason the schema's flat enum is safe: the
    refusal carries the per-sport rule the enum cannot."""
    bad = (Step("exercise", duration_secs=40.0, exercise="squat"),)

    with pytest.raises(ComposeError, match="not a step kind for a running"):
        compose.compose("Bad", bad, sport="running")


def test_the_refusal_lists_that_sports_kinds():
    with pytest.raises(ComposeError, match="Valid kinds: exercise, rest"):
        compose.compose("Bad", (Step("warmup", duration_secs=600.0),), sport="strength")


def test_a_strength_step_measured_in_distance_is_refused():
    """Nothing in a circuit is measured in distance, and the empty
    `DISTANCE_BUILDERS_BY_SPORT` entry is what says so."""
    bad = (Step("rest", distance_m=100.0),)

    with pytest.raises(ComposeError, match="measured in time, not distance"):
        compose.compose("Bad", bad, sport="strength")


def test_an_exercise_measured_in_distance_is_refused():
    bad = (Step("exercise", distance_m=100.0, exercise="squat"),)

    with pytest.raises(ComposeError, match="measured in time, not distance"):
        compose.compose("Bad", bad, sport="strength")


def test_an_exercise_with_no_duration_is_refused():
    bad = (Step("exercise", exercise="squat"),)

    with pytest.raises(ComposeError, match="needs duration_secs"):
        compose.compose("Bad", bad, sport="strength")


@pytest.mark.parametrize("bad", [0.0, -40.0, math.inf, math.nan])
def test_a_non_positive_exercise_duration_is_refused(bad):
    steps = (Step("exercise", duration_secs=bad, exercise="squat"),)

    with pytest.raises(ComposeError):
        compose.compose("Bad", steps, sport="strength")


def test_a_strength_work_step_with_no_category_is_refused():
    """Reached by composing a good circuit and breaking it, the way the four
    unreachable §8 cases are -- and it is the path `create` takes when a
    structure comes back from a conversation rather than from the composer."""
    structure = compose.compose("Thu circuit", CIRCUIT, sport="strength")
    broken = copy.deepcopy(structure)
    group = broken["workoutSegments"][0]["workoutSteps"][0]
    group["workoutSteps"][0]["category"] = ""

    with pytest.raises(ComposeError, match="no exercise category"):
        compose.validate_structure(broken)


def test_the_category_check_exempts_a_rest_step():
    """The control. A check that refused rests too would refuse every circuit,
    and would read as working because the refusal message looks deliberate."""
    structure = compose.compose("Thu circuit", CIRCUIT, sport="strength")

    compose.validate_structure(structure)  # the rests carry no category


def test_the_category_check_does_not_fire_on_a_running_session():
    """A running interval carries no category and must not need one."""
    structure = compose.compose("Thu threshold", SESSION)

    compose.validate_structure(structure)


def test_an_unknown_sport_is_refused():
    with pytest.raises(ComposeError, match="unknown sport"):
        compose.compose("Bad", EASY_RUN, sport="rowing")


def test_the_running_default_is_byte_identical_to_asking_for_it():
    """`sport` arrived with a default so that every caller written before
    strength existed composes the same structure after it. That is a claim about
    bytes, so it is asserted as one."""
    implicit = compose.compose("Thu threshold", SESSION)
    explicit = compose.compose("Thu threshold", SESSION, sport="running")

    assert json.dumps(implicit, sort_keys=True) == json.dumps(explicit, sort_keys=True)


def test_the_step_kind_union_covers_both_sports_and_nothing_else():
    """`STEP_KINDS` is what the tool's enum is built from, so a kind missing
    here is a kind the agent cannot ask for however well it is implemented."""
    assert set(compose.STEP_KINDS) == {
        "warmup",
        "interval",
        "recovery",
        "cooldown",
        "exercise",
        "rest",
    }
    assert set(compose.STEP_KINDS_BY_SPORT["strength"]) == {"exercise", "rest"}
    assert "exercise" not in compose.STEP_KINDS_BY_SPORT["running"]
