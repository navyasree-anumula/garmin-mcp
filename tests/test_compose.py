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
    """**Premise changed at v1.6, not loosened.** A pace range is now allowed,
    so this can no longer assert the old "no targets" wording -- but a
    heart-rate zone is still refused, and for the reason v0 gave: this
    account's zones have never been fetched. The refusal must also say what IS
    allowed, or a reader learns only that it was told no."""
    structure = compose.compose("Easy 40", EASY_RUN)
    steps_of(structure)[0]["targetType"] = {
        "workoutTargetTypeId": 4,
        "workoutTargetTypeKey": "heart.rate.zone",
        "displayOrder": 4,
    }

    with pytest.raises(ComposeError) as caught:
        compose.validate_structure(structure)

    message = str(caught.value)
    assert "heart.rate.zone" in message
    assert "zone-based" in message
    assert "pace range" in message


def test_v6_a_missing_target_is_refused_too():
    """Not the same case as a wrong target, and worth its own assertion: an
    absent `targetType` is what an upstream change to the builders' default
    would produce. It must not fall through the pace branch into acceptance."""
    structure = compose.compose("Easy 40", EASY_RUN)
    del steps_of(structure)[0]["targetType"]

    with pytest.raises(ComposeError, match="zone-based"):
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
    basis -- and the basis has to explain the 0, not just describe the method.

    **Premise changed 2026-10-31, not loosened.** This asserted the basis said
    "unverified", which was right while Garmin's recomputing was an open
    question. Workout 1746800115 settled it -- Garmin does not recompute -- so
    the basis now has to say the opposite, and saying nothing would be the
    regression."""
    seconds, basis = compose.estimate_duration((Step("interval", distance_m=10000.0),))

    assert seconds == 0
    assert "contribute 0" in basis
    assert "no measured pace" in basis
    assert "does not recompute" in basis


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
    """`recovery` is a running kind and means nothing in a circuit. `warmup`
    used to stand here and no longer can, because a strength session may now
    have one -- the test moved rather than being loosened."""
    with pytest.raises(
        ComposeError, match="Valid kinds: exercise, warmup, rest, cooldown"
    ):
        compose.compose(
            "Bad", (Step("recovery", duration_secs=120.0),), sport="strength"
        )


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
    assert set(compose.STEP_KINDS_BY_SPORT["strength"]) == {
        "exercise",
        "warmup",
        "rest",
        "cooldown",
    }
    assert "exercise" not in compose.STEP_KINDS_BY_SPORT["running"]
    # The union did not change when strength gained warmup and cooldown: both
    # were already in it from running. So the published enum is untouched, and
    # the per-sport rule is doing all the work -- which is the arrangement the
    # flat enum was accepted on.
    assert "recovery" not in compose.STEP_KINDS_BY_SPORT["strength"]
    assert "interval" not in compose.STEP_KINDS_BY_SPORT["strength"]


# --------------------------------------------------------------------------
# A strength session may now warm up and cool down (§5)
#
# v1.6 offered `exercise` and `rest` only, so the first live 30-minute session
# came out as a bare circuit and the human was told to warm up on their own.
# The builders are upstream's and generic across sports; **Garmin's acceptance
# of a warmup step inside a strength_training workout is unverified** until the
# next live strength create, which was going to happen anyway.
# --------------------------------------------------------------------------

FULL_SESSION = (
    Step("warmup", duration_secs=300.0),
    Step(
        "repeat",
        iterations=3,
        repeat=(
            Step("exercise", duration_secs=40.0, exercise="goblet squat"),
            Step("rest", duration_secs=20.0),
        ),
    ),
    Step("cooldown", duration_secs=180.0),
)


def test_a_strength_session_composes_with_a_warmup_and_cooldown():
    structure = compose.compose("Thu session", FULL_SESSION, sport="strength")
    steps = steps_of(structure)

    assert steps[0]["stepType"]["stepTypeKey"] == "warmup"
    assert steps[0]["endConditionValue"] == 300.0
    assert steps[-1]["stepType"]["stepTypeKey"] == "cooldown"
    assert steps[-1]["endConditionValue"] == 180.0


def test_the_full_session_numbers_its_steps_contiguously():
    """Warmup, the group, its two children, cooldown."""
    structure = compose.compose("Thu session", FULL_SESSION, sport="strength")

    assert flat_orders(structure) == [1, 2, 3, 4, 5]


def test_the_category_check_exempts_a_warmup_and_a_cooldown():
    """They name no exercise by design, like a rest. An exemption that was too
    narrow would refuse every session with a warmup; one too broad would
    re-permit the unnamed work step the check exists to catch -- so both
    directions are asserted, here and in the test below."""
    structure = compose.compose("Thu session", FULL_SESSION, sport="strength")

    compose.validate_structure(structure)


def test_the_category_check_still_catches_an_unnamed_work_step():
    """The other direction. Adding warmup and cooldown to the exempt set must
    not have widened it to the step that matters."""
    structure = compose.compose("Thu session", FULL_SESSION, sport="strength")
    broken = copy.deepcopy(structure)
    group = broken["workoutSegments"][0]["workoutSteps"][1]
    group["workoutSteps"][0]["category"] = ""

    with pytest.raises(ComposeError, match="no exercise category"):
        compose.validate_structure(broken)


def test_the_estimate_counts_the_warmup_and_cooldown():
    """300 + 3 x (40 + 20) + 180."""
    estimated, _ = compose.estimate_duration(FULL_SESSION)

    assert estimated == 660


def test_a_strength_warmup_carrying_an_exercise_name_is_refused():
    """Garmin would drop the name and the watch would show a nameless step --
    the same silent partial an exercise name on a rest step is refused for."""
    bad = (Step("warmup", duration_secs=300.0, exercise="goblet squat"),)

    with pytest.raises(ComposeError, match="carries an exercise name"):
        compose.compose("Bad", bad, sport="strength")


def test_a_strength_warmup_measured_in_distance_is_refused():
    bad = (Step("warmup", distance_m=1000.0),)

    with pytest.raises(ComposeError, match="measured in time, not distance"):
        compose.compose("Bad", bad, sport="strength")


# ------------------------- the catalogue search ---------------------------


def test_the_search_returns_its_matches_and_the_total():
    """The total is returned beside the capped list rather than inferred from
    its length, because the two differ and only one of them is the answer to
    "is that all of them"."""
    rows, total = compose.search_exercises("press", limit=3)

    assert len(rows) == 3
    assert total > 3


def test_the_search_returns_everything_when_it_fits():
    rows, total = compose.search_exercises("goblet squat", limit=25)

    assert len(rows) == total


def test_the_search_finds_nothing_without_raising():
    """An empty result is an answer, not an error -- the tool layer decides
    how to present it. Refusing here would make "does this exist?" unaskable."""
    rows, total = compose.search_exercises("nonsense exercise xyz")

    assert rows == ()
    assert total == 0


@pytest.mark.parametrize("term", [None, "", "   "])
def test_a_search_with_no_words_is_refused(term):
    with pytest.raises(ComposeError, match="needs something to search for"):
        compose.search_exercises(term)


@pytest.mark.parametrize("limit", [0, -5])
def test_a_search_limit_below_one_is_refused(limit):
    with pytest.raises(ComposeError, match="at least 1"):
        compose.search_exercises("squat", limit=limit)


def test_the_search_is_a_pure_lookup_over_the_catalogue():
    """Asserted against the catalogue itself: every row the search returns is
    an entry that was already there, so the search cannot invent a name that
    `resolve_exercise` would then refuse."""
    rows, _ = compose.search_exercises("squat", limit=40)

    for row in rows:
        assert row in compose.EXERCISES


# --------------------------------------------------------------------------
# Pace targets (§5/§8, v1.6). The one target v1.6 allows, and the many it
# does not. Every refusal below is paired with the valid variant composing,
# because a validator only ever seen to refuse might be refusing everything.
# --------------------------------------------------------------------------


PACED_RUN = (
    Step("warmup", duration_secs=600.0),
    Step("interval", distance_m=5000.0, pace_min_km=("4:20", "4:40")),
    Step("cooldown", duration_secs=300.0),
)


def paced_step_of(structure, order=2):
    for item, _ in compose._flatten(steps_of(structure)):
        if item.get("stepOrder") == order:
            return item
    raise AssertionError(f"no step with stepOrder {order}")


def test_the_pace_conversion_is_pinned_to_literals():
    """**Pinned on purpose, both directions** (§8, v1.6). The conversion is one
    line of arithmetic -- 1000 / seconds-per-km, rounded to 3 decimals -- which
    is exactly the kind of line a refactor changes without anybody noticing. If
    these numbers move, what a pace MEANS has moved, and the watch would alert
    against a speed nobody approved."""
    target = compose.pace_target(["4:20", "4:40"])

    # min/km in, m/s out: the bytes Garmin stores.
    assert target.faster_m_s == 3.846
    assert target.slower_m_s == 3.571

    # and back out as min/km: the words a human approves.
    assert target.rendered == "4:20-4:40/km"


def test_the_conversion_rounds_before_anything_reads_it():
    """Pass A.3 / B.1: the rounding has to happen upstream of the approval
    marker. If the structure carried 3.8461538... and the marker were computed
    over a rounded copy, the preview's marker and the create's would disagree
    about the same session."""
    target = compose.pace_target(["4:20", "4:40"])

    assert target.faster_m_s == round(target.faster_m_s, 3)
    assert target.slower_m_s == round(target.slower_m_s, 3)

    structure = compose.compose("Pinned", PACED_RUN)
    step = paced_step_of(structure)
    assert step[compose.PACE_CONTRACT.faster_bound_field] == 3.846
    assert step[compose.PACE_CONTRACT.slower_bound_field] == 3.571


def test_the_approval_marker_survives_a_float_round_trip():
    """The `600.0`-vs-`600` bug generalised, and the regression test Mahi asked
    for. A preview's marker is computed here; a create's is computed over the
    structure after it has been through JSON. A non-whole float is the case the
    old bug never covered -- measured stable, and now asserted so it stays
    that way."""
    structure = compose.compose("Round trip", PACED_RUN)

    before = compose.approval_for(structure)
    after = compose.approval_for(json.loads(json.dumps(structure)))

    assert before == after
    assert paced_step_of(json.loads(json.dumps(structure)))[
        compose.PACE_CONTRACT.faster_bound_field
    ] == 3.846


def test_a_paced_step_carries_the_whole_contract_from_one_constant():
    """Every part of Garmin's pace contract is unverified, so every part of it
    comes out of `PACE_CONTRACT` -- the point being that a wrong one is wrong
    in a single place."""
    structure = compose.compose("Tempo", PACED_RUN)
    step = paced_step_of(structure)

    assert step["targetType"] == {
        "workoutTargetTypeId": compose.PACE_CONTRACT.target_id,
        "workoutTargetTypeKey": compose.PACE_CONTRACT.target_key,
        "displayOrder": compose.PACE_CONTRACT.display_order,
    }
    assert compose.PACE_CONTRACT.target_id == 6
    assert compose.PACE_CONTRACT.target_key == "pace.zone"


def test_the_faster_bound_is_the_larger_speed():
    """The one that would be invisible if it were wrong: a swapped pair still
    composes, still uploads, and turns the easy end of the range into the hard
    end on the watch. Asserted through the constant, so it keeps holding when
    the read-back settles which field Garmin wants first."""
    structure = compose.compose("Tempo", PACED_RUN)
    step = paced_step_of(structure)

    faster = step[compose.PACE_CONTRACT.faster_bound_field]
    slower = step[compose.PACE_CONTRACT.slower_bound_field]

    assert faster > slower


def test_a_pace_target_works_inside_a_repeat_group():
    """§15's 7.b was a claim, not an observation: a repeat group's children go
    through the same builder, so a pace inside one should need no extra code.
    This is the observation."""
    steps = (
        Step("warmup", duration_secs=600.0),
        Step(
            "repeat",
            iterations=4,
            repeat=(
                Step("interval", distance_m=800.0, pace_min_km=("4:00", "4:10")),
                Step("recovery", duration_secs=120.0),
            ),
        ),
    )
    structure = compose.compose("Intervals", steps)

    inner = paced_step_of(structure, order=3)
    assert inner["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert inner[compose.PACE_CONTRACT.faster_bound_field] == 4.167

    recovery = paced_step_of(structure, order=4)
    assert recovery["targetType"]["workoutTargetTypeKey"] == compose.NO_TARGET_KEY
    assert compose.PACE_CONTRACT.faster_bound_field not in recovery


def test_a_repeat_group_itself_cannot_carry_a_pace():
    """Garmin stores a target per step, so a pace on the group would be dropped
    without being refused -- the same failure as an exercise name on the wrong
    kind, and refused the same way."""
    steps = (
        Step(
            "repeat",
            iterations=3,
            repeat=(Step("interval", distance_m=400.0),),
            pace_min_km=("4:00", "4:10"),
        ),
    )

    with pytest.raises(ComposeError, match="no target of its own"):
        compose.compose("Bad", steps)


def test_a_session_with_no_pace_composes_exactly_what_it_did_before():
    """The relaxation must not change an untargeted session by one byte. A
    strength rest step is the sharp case: its builder has no `target_type`
    parameter at all, so a `None` target passed down to it would be a
    TypeError rather than a wrong workout."""
    structure = compose.compose("Easy 40", EASY_RUN)
    step = steps_of(structure)[0]

    assert step["targetType"]["workoutTargetTypeKey"] == compose.NO_TARGET_KEY
    assert compose.PACE_CONTRACT.faster_bound_field not in step

    strength = compose.compose(
        "Circuit",
        (
            Step("exercise", duration_secs=40.0, exercise="Goblet Squat"),
            Step("rest", duration_secs=20.0),
        ),
        sport="strength",
    )
    assert len(steps_of(strength)) == 2


@pytest.mark.parametrize(
    "kind",
    ["warmup", "rest", "cooldown"],
)
def test_a_pace_on_a_strength_step_is_refused(kind):
    """1.d, and it is load-bearing rather than belt-and-braces: strength's
    `warmup` and `cooldown` are built by RUNNING's builders, which do accept a
    target, so a pace in a circuit is expressible and has to be stopped by
    sport."""
    steps = (Step(kind, duration_secs=60.0, pace_min_km=("4:20", "4:40")),)

    with pytest.raises(ComposeError, match="pace target is for a running step"):
        compose.compose("Bad", steps, sport="strength")


def test_a_pace_on_a_strength_exercise_step_is_refused_not_dropped():
    """Checked separately because `exercise` returns from its own builder
    early: if the sport check sat below that return, a pace here would vanish
    silently instead of being refused."""
    steps = (
        Step(
            "exercise",
            duration_secs=40.0,
            exercise="Goblet Squat",
            pace_min_km=("4:20", "4:40"),
        ),
    )

    with pytest.raises(ComposeError, match="pace target is for a running step"):
        compose.compose("Bad", steps, sport="strength")


def test_the_same_strength_session_composes_without_the_pace():
    """The control for the refusals above. A validator only seen to refuse
    might be refusing the session rather than the pace."""
    structure = compose.compose(
        "Circuit",
        (
            Step("warmup", duration_secs=60.0),
            Step("exercise", duration_secs=40.0, exercise="Goblet Squat"),
            Step("rest", duration_secs=20.0),
        ),
        sport="strength",
    )

    assert len(steps_of(structure)) == 3


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        (["4:75", "5:00"], "seconds must be under 60"),
        (["4:20.5", "4:40"], "not a pace"),
        (["4:30", "4:30"], "two different bounds"),
        (["4:40", "4:20"], "faster bound first"),
        (["2:00", "4:40"], "outside the pace band"),
        (["4:20", "15:00"], "outside the pace band"),
        (["4:20"], "range of two paces"),
        ([], "range of two paces"),
        ("4:20", "range of two paces"),
        ([260, 280], "written as mm:ss"),
    ],
)
def test_a_pace_range_is_refused_rather_than_repaired(bounds, expected):
    """All five of §15's edge cases, plus the shapes that are not a range at
    all. Refused, never normalised: a pace quietly moved is one the human
    approved in words that no longer describe the workout."""
    with pytest.raises(ComposeError, match=expected):
        compose.pace_target(bounds)


def test_an_inverted_range_is_not_silently_sorted():
    """Spelled out as its own test because sorting is the tempting fix. It
    would be kinder to a caller that typed the bounds backwards and would hide
    a caller that has UNDERSTOOD the field backwards, which is the one worth
    finding."""
    with pytest.raises(ComposeError) as caught:
        compose.pace_target(["4:40", "4:20"])

    assert "['4:20', '4:40']" in str(caught.value)


def test_a_pace_renders_from_the_conversion_not_from_the_input():
    """Pass A.1's guarantee, at the level it is made: the rendering comes back
    canonical, which is only possible if it was produced from the parsed number
    rather than echoed from the string."""
    target = compose.pace_target(["04:20", "4:40"])

    assert target.rendered == "4:20-4:40/km"


def test_the_basis_stops_claiming_there_is_no_pace():
    """3.e / A.2. `estimate_duration` cannot see targets at all, so its basis
    sentence was false the moment a pace could exist.

    **Premise changed 2026-10-31 (a) -> (b).** Option (a) changed the sentence
    and left the arithmetic; it was reversed the same day, once workout
    1746800115 showed Garmin does not recompute the duration on upload. A step
    given a pace is now estimated from it, and one without a pace still
    contributes 0 -- which is the half of the old behaviour that survives."""
    unpaced = (
        Step("warmup", duration_secs=600.0),
        Step("interval", distance_m=5000.0),
    )
    paced_secs, paced_basis = compose.estimate_duration(PACED_RUN)
    plain_secs, plain_basis = compose.estimate_duration(unpaced)

    assert "this account has no measured pace" in plain_basis
    assert "this account has no measured pace" not in paced_basis
    assert "arithmetic on the prescription" in paced_basis
    assert paced_secs == 2250      # 600 + 300 timed, + 5000m at 4:30/km
    assert plain_secs == 600


def test_a_pace_is_what_makes_a_distance_step_estimable():
    """**The reversal, asserted in one place** (option (b), 2026-10-31). The
    same session estimates differently with the pace and without it, and the
    difference is exactly the distance step. Under option (a) this test
    asserted the two were equal; it is kept pointing the other way rather than
    deleted, because the pair of numbers is what the decision is about."""
    unpaced = tuple(
        Step(s.kind, duration_secs=s.duration_secs, distance_m=s.distance_m)
        for s in PACED_RUN
    )

    paced_secs = compose.estimate_duration(PACED_RUN)[0]
    plain_secs = compose.estimate_duration(unpaced)[0]

    assert paced_secs > plain_secs
    assert paced_secs - plain_secs == 1350


def test_an_all_timed_paced_session_says_the_pace_is_unused():
    """The basis must say it in the case where nothing was skipped either,
    because the claim being corrected is about the pace and not about the
    distance steps."""
    steps = (Step("interval", duration_secs=1200.0, pace_min_km=("4:20", "4:40")),)

    _, basis = compose.estimate_duration(steps)

    assert "not used to estimate" in basis


def test_a_hand_built_pace_target_in_a_strength_session_is_refused():
    """The relaxation is per sport AND per target type. A structure that came
    back from a conversation rather than from the composer is exactly the path
    `create` takes, and a global loosening would let a strength pace through."""
    structure = compose.compose(
        "Circuit",
        (
            Step("exercise", duration_secs=40.0, exercise="Goblet Squat"),
            Step("rest", duration_secs=20.0),
        ),
        sport="strength",
    )
    step = steps_of(structure)[0]
    step["targetType"] = compose.pace_target(["4:20", "4:40"]).target_block()
    step.update(compose.pace_target(["4:20", "4:40"]).target_values())

    with pytest.raises(ComposeError, match="running only"):
        compose.validate_structure(structure)


def test_a_target_value_with_no_target_type_is_refused():
    """Half a target is not a target. A value the watch has no rule for is
    worse than no value, because it reads as a prescription in the payload."""
    structure = compose.compose("Easy 40", EASY_RUN)
    steps_of(structure)[0][compose.PACE_CONTRACT.faster_bound_field] = 3.846

    with pytest.raises(ComposeError, match="no target type"):
        compose.validate_structure(structure)


def test_a_pace_target_missing_one_bound_is_refused():
    structure = compose.compose("Tempo", PACED_RUN)
    del paced_step_of(structure)[compose.PACE_CONTRACT.slower_bound_field]

    with pytest.raises(ComposeError, match="only one bound"):
        compose.validate_structure(structure)


def test_a_swapped_pair_is_refused_by_the_validator():
    """The composer cannot produce this -- it sorts nothing and refuses an
    inverted range -- so it is reached the way `create` would reach it, from a
    structure built somewhere else."""
    structure = compose.compose("Tempo", PACED_RUN)
    step = paced_step_of(structure)
    step[compose.PACE_CONTRACT.faster_bound_field] = 3.571
    step[compose.PACE_CONTRACT.slower_bound_field] = 3.846

    with pytest.raises(ComposeError, match="must be the larger number"):
        compose.validate_structure(structure)


@pytest.mark.parametrize("word", ["squat", "press", "curl", "raise", "row"])
def test_every_name_the_search_returns_resolves_unambiguously(word):
    """§8, v1.6. The search is only useful if its output can be handed straight
    back to a step, and that is two functions agreeing rather than either being
    right on its own: `search_exercises` must not return a name that
    `resolve_exercise` would then refuse as ambiguous."""
    rows, _ = compose.search_exercises(word, limit=40)

    assert rows
    for row in rows:
        resolved = compose.resolve_exercise(row["name"])
        assert resolved["name"] == row["name"]


def test_the_basis_no_longer_hedges_about_garmin_recomputing():
    """**Settled live on 2026-10-31**, workout 1746800115: the server computed
    27:30 and Connect displayed 27:30 unchanged, so Garmin does not recompute
    `estimatedDurationInSecs` on upload. The hedge is asserted gone rather than
    just deleted, because an unverified claim that comes back is how a measured
    fact gets un-measured -- and the consequence matters: the number the
    calendar shows is ours and final."""
    with_distance = (
        Step("warmup", duration_secs=600.0),
        Step("interval", distance_m=5000.0),
    )

    for steps in (with_distance, PACED_RUN):
        _, basis = compose.estimate_duration(steps)

        assert "may recompute" not in basis
        assert "unverified" not in basis
        assert "does not recompute" in basis


def test_the_midpoint_is_taken_in_min_per_km_not_in_m_s():
    """Pinned because the two are different numbers: averaging the paces and
    averaging the speeds differ by a third of a second per km over a 20-second
    range, since one is the harmonic mean of the other. The pace is the unit
    the human approved, so the midpoint is taken there -- 4:20 to 4:40 gives
    4:30, which is the answer anybody would give out loud."""
    target = compose.pace_target(["4:20", "4:40"])

    assert target.midpoint_secs_per_km == 270.0
    assert target.midpoint_rendered == "4:30/km"

    speed_midpoint = (target.faster_m_s + target.slower_m_s) / 2.0
    assert 1000.0 / speed_midpoint != target.midpoint_secs_per_km


def test_a_paced_distance_step_is_arithmetic_on_the_range():
    """5 km at the midpoint of 4:20-4:40 is 5 x 270 seconds. Pinned, because
    this number now lands on Garmin's calendar and nothing downstream corrects
    it."""
    target = compose.pace_target(["4:20", "4:40"])

    assert target.seconds_for(5000.0) == 1350.0
    assert target.seconds_for(800.0) == 216.0


def test_a_paced_step_inside_a_repeat_is_counted_every_round():
    """The estimate multiplies by iterations and so does the count in the
    basis: four 800s estimated from a pace is four facts about the session the
    athlete runs, not one about how it was written."""
    steps = (
        Step("warmup", duration_secs=600.0),
        Step(
            "repeat",
            iterations=4,
            repeat=(
                Step("interval", distance_m=800.0, pace_min_km=("4:00", "4:10")),
                Step("recovery", duration_secs=120.0),
            ),
        ),
    )

    seconds, basis = compose.estimate_duration(steps)

    # 600 warmup + 4 x (800m at 4:05/km = 196s) + 4 x 120 recovery
    assert seconds == 1864
    assert "4 distance step(s) are estimated" in basis


def test_a_session_with_some_paces_and_some_not_says_both():
    """The mixed case is the one a basis sentence gets wrong: it has to report
    what was estimated AND what contributed nothing, because a reader who sees
    only one of the two misreads the number."""
    steps = (
        Step("interval", distance_m=5000.0, pace_min_km=("4:20", "4:40")),
        Step("interval", distance_m=1000.0),
    )

    seconds, basis = compose.estimate_duration(steps)

    assert seconds == 1350
    assert "1 distance step(s) are estimated" in basis
    assert "1 distance step(s) contribute 0" in basis


def test_an_unpaced_distance_step_still_contributes_nothing():
    """The half of option (a) that survived (b). Without a pace there is
    nothing to compute from, and this account has never had its pace measured,
    so the step contributes 0 and the basis says why."""
    seconds, basis = compose.estimate_duration((Step("interval", distance_m=5000.0),))

    assert seconds == 0
    assert "contribute 0" in basis
    assert "no measured pace" in basis
