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
