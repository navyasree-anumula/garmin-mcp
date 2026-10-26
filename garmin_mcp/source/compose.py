"""Composing a running session, and refusing one that is malformed (§5, v1.5).

**This module makes no requests.** It takes a step list, builds the structure
Garmin's upload wants, and hands it back. No tokens, no budget, no lock, no 429
-- which is why §8 names the preview as the first write-path behaviour with real
unit tests, and why the one supervised live cycle later is spent confirming
Garmin's acceptance rather than finding our own bugs.

It imports `garminconnect.workout`, which is the §11 rule rather than an
exception to it: composition belongs in `source/`, beside the metric map. It
imports nothing from `client.py`, and `tests/test_compose.py` asserts that by
walking the AST -- "makes no requests" is a claim that rots the first time
somebody adds a convenient lookup.

**v0 is time and distance with no target.** `TargetType` offers only zone-based
targets, `get_heart_rate_zones` has never been run against this account, and
absolute ranges through `ExecutableStep`'s `extra="allow"` are unverified
against what Garmin accepts. So a target is refused rather than guessed, and the
first live cycle confirms one unknown instead of three.

**What this module does not do is design the session.** It composes what it is
told. Which distance, how many reps, how long the recovery -- those need how the
legs feel and how much time there is, which no endpoint holds, so they belong in
the conversation (§1).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

from garminconnect.workout import (
    RunningWorkout,
    WorkoutSegment,
    create_cooldown_step,
    create_distance_interval_step,
    create_interval_step,
    create_recovery_step,
    create_repeat_group,
    create_warmup_step,
)

from .errors import GarminSourceError

#: The step kinds v0 composes, each mapped to the builder that makes it. Keyed
#: rather than branched on, so the tool's enum is derived from this dict and a
#: new kind cannot arrive in the schema without a builder behind it.
TIME_BUILDERS = {
    "warmup": create_warmup_step,
    "interval": create_interval_step,
    "recovery": create_recovery_step,
    "cooldown": create_cooldown_step,
}

#: Only an interval may be measured in distance. A warmup or cooldown by
#: distance is expressible upstream and is left out of v0 on purpose: every kind
#: we offer is a kind the live cycle has to confirm, and the list should be as
#: short as the loop allows.
DISTANCE_BUILDERS = {
    "interval": create_distance_interval_step,
}

STEP_KINDS = tuple(TIME_BUILDERS)

#: Garmin's own id/key for "no target", which the builders already default to
#: when no target is passed (read from `garminconnect/workout.py`, not assumed).
#: Named here because `validate_structure` asserts it rather than trusting it.
NO_TARGET_KEY = "no.target"
NO_TARGET_ID = 1


class ComposeError(GarminSourceError):
    """A session we refuse to compose, or a structure we refuse to call valid.

    Subclasses `GarminSourceError` so the tool layer maps it the same way as
    every other source error, without importing anything from here.
    """


@dataclass(frozen=True)
class Step:
    """One step of a session, before it becomes Garmin's shape.

    Exactly one of `duration_secs` and `distance_m` is set -- a step measured
    both ways has two end conditions and Garmin takes one. `repeat` carries the
    nested steps when this entry is a repeat group, in which case `kind` is
    `"repeat"` and neither measure is set.
    """

    kind: str
    duration_secs: float | None = None
    distance_m: float | None = None
    iterations: int | None = None
    repeat: tuple["Step", ...] = field(default_factory=tuple)


def _positive(value: float, what: str) -> None:
    """`float` accepts NaN and infinity, and both survive JSON serialisation.

    `endConditionValue` is a float, so neither is caught by typing. A NaN
    duration would reach Garmin as `NaN` and be rejected there at best; at worst
    it lands on a calendar. Checked here rather than at the boundary because
    this is the only place the number is known to be a measure.
    """
    if not math.isfinite(value):
        raise ComposeError(f"{what} must be a finite number, not {value!r}.")
    if value <= 0:
        raise ComposeError(f"{what} must be greater than zero, not {value!r}.")


def _build_step(step: Step, order: int) -> Any:
    """One step, built by the builder its shape names.

    `stepOrder` is assigned by the caller's counter and never taken from the
    step: a duplicated `stepOrder` is one of the malformed shapes §8 asks us to
    refuse, and a value the caller cannot supply is a value the caller cannot
    duplicate. `validate_structure` still checks it, for a structure that
    arrives from somewhere else.
    """
    if step.kind == "repeat":
        raise ComposeError("a repeat group cannot be nested inside another repeat group.")

    if step.duration_secs is not None and step.distance_m is not None:
        raise ComposeError(
            f"step {order} ({step.kind}) sets both duration_secs and distance_m. "
            "A step ends one way or the other, never both."
        )

    if step.duration_secs is not None:
        builder = TIME_BUILDERS.get(step.kind)
        if builder is None:
            raise ComposeError(
                f"unknown step kind {step.kind!r}. Valid kinds: {', '.join(STEP_KINDS)}."
            )
        _positive(step.duration_secs, f"step {order} ({step.kind}) duration_secs")
        return builder(step.duration_secs, order)

    if step.distance_m is not None:
        builder = DISTANCE_BUILDERS.get(step.kind)
        if builder is None:
            raise ComposeError(
                f"step {order}: only an {', '.join(DISTANCE_BUILDERS)} step may be "
                f"measured in distance, not a {step.kind!r}. Give it duration_secs "
                "instead."
            )
        _positive(step.distance_m, f"step {order} ({step.kind}) distance_m")
        return builder(step.distance_m, order)

    raise ComposeError(
        f"step {order} ({step.kind}) has neither duration_secs nor distance_m. "
        "A step needs an end condition."
    )


def _build_repeat(step: Step, order: int) -> tuple[Any, int]:
    """A repeat group, and the next free `stepOrder` after it.

    The children share the parent's numbering -- Garmin's `stepOrder` is flat
    across the segment, not per-group -- so this returns the counter it consumed
    rather than letting the caller guess.
    """
    if step.iterations is None:
        raise ComposeError(f"step {order}: a repeat group needs iterations.")
    if step.iterations < 2:
        raise ComposeError(
            f"step {order}: a repeat group needs at least 2 iterations, not "
            f"{step.iterations}. One iteration is not a repeat -- inline the step."
        )
    if not step.repeat:
        raise ComposeError(
            f"step {order}: a repeat group with no steps repeats nothing."
        )

    next_order = order + 1
    children = []
    for child in step.repeat:
        children.append(_build_step(child, next_order))
        next_order += 1

    return create_repeat_group(step.iterations, children, order), next_order


def compose(
    name: str, steps: tuple[Step, ...], description: str | None = None
) -> dict[str, Any]:
    """The structure Garmin's upload wants, built from a step list.

    Validates its own output before returning it. That looks redundant -- we
    just built it -- and it is not: the builders are upstream code, and a change
    in them is something our tests should catch rather than Garmin. It is also
    exactly the check `create` will run on an approved structure, so the rule
    lives in one place from the start.
    """
    if not name or not name.strip():
        raise ComposeError("a workout needs a name.")
    if not steps:
        raise ComposeError("a workout with no steps is not a session.")

    built: list[Any] = []
    order = 1
    for step in steps:
        if step.kind == "repeat":
            group, order = _build_repeat(step, order)
            built.append(group)
        else:
            built.append(_build_step(step, order))
            order += 1

    estimated, _ = estimate_duration(steps)

    workout = RunningWorkout(
        workoutName=name.strip(),
        sportType={"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
        estimatedDurationInSecs=estimated,
        description=description,
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={
                    "sportTypeId": 1,
                    "sportTypeKey": "running",
                    "displayOrder": 1,
                },
                workoutSteps=built,
            )
        ],
    )

    structure = workout.to_dict()
    validate_structure(structure)
    return structure


def _numeric_canonical(value: Any) -> Any:
    """Whole-valued floats become ints, everywhere, at any depth.

    **This is what makes the marker survive a client.** JavaScript has a single
    number type, so `JSON.stringify(600.0)` emits `600`. Our steps carry
    `endConditionValue` as a float, because that is the library model's type --
    so a preview emits `600.0`, the client parses and re-serialises it as `600`,
    and the structure that comes back to `create` is numerically identical and
    textually different.

    Found on 2026-10-27 by the first real round trip through Claude Desktop,
    which refused its own preview. **No test could have caught it**: Python's
    own `json` round trip preserves `600.0`, and every test passed the structure
    in-process as an object. The marker's whole job is to survive a transport
    and nothing exercised one.

    Normalising for the *hash* only. The structure sent to Garmin is the one the
    client handed us, not this view of it -- rewriting a payload on its way out
    would be a second composition, which is the thing §5 forbids.
    """
    if isinstance(value, bool):
        return value  # bool is an int subclass; leave it alone
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _numeric_canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_numeric_canonical(item) for item in value]
    return value


def approval_for(structure: dict[str, Any]) -> str:
    """A fingerprint of the structure a human was shown.

    §5: the create action takes the exact structure the preview returned, never
    a re-composition from the same arguments -- otherwise the human approves one
    thing and a second composition is what lands, which is the right value
    recorded against the wrong event.

    Canonical JSON over a numerically normalised view, so the marker depends on
    the content and not on how a client happens to render it. Key order and
    whitespace were the obvious hazards; `600.0` against `600` was the one that
    actually bit, and `_numeric_canonical` explains it.

    It is **not** a security boundary. It cannot be, since whoever can call
    `create` can also call `preview` and get a fresh marker. What it stops is
    the accident: an edited structure, a stale structure from an earlier turn,
    or a re-composition that drifted. That is the failure §5 names, and it is the
    one that actually happens.

    The failure direction is deliberate. A marker that is too strict refuses a
    write; one that is too loose lets an edited session through. The first costs
    a round trip, the second costs a wrong workout on a watch -- so where a
    transformation cannot be proven harmless, it stays a mismatch.
    """
    canonical = json.dumps(
        _numeric_canonical(structure), sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def estimate_duration(steps: tuple[Step, ...]) -> tuple[int, str]:
    """Seconds, and the sentence saying how that number was arrived at.

    **Time-based steps only.** A distance step's duration is unknowable without
    a pace, and this account has no measured pace -- `get_heart_rate_zones` and
    the pace endpoints have never been run. Assuming one would put a fabricated
    number in a field the calendar may display, so a distance step contributes
    zero and the basis says so.

    That means an all-distance session estimates 0, which reads as broken unless
    the basis is read with it. The two are returned together for exactly that
    reason, and `estimated_duration_secs` is never surfaced without its basis.

    Whether Garmin recomputes this on upload is **unverified** -- the supervised
    live cycle settles it, and until then the basis says that too.
    """
    timed = 0.0
    distance_steps = 0
    for step in steps:
        if step.kind == "repeat":
            child_total, child_distance = _repeat_totals(step)
            timed += child_total
            distance_steps += child_distance
        elif step.duration_secs is not None:
            timed += step.duration_secs
        elif step.distance_m is not None:
            distance_steps += 1

    basis = "Sum of the time-based steps."
    if distance_steps:
        basis = (
            f"Sum of the time-based steps only. {distance_steps} distance step(s) "
            "contribute 0: this account has no measured pace, and assuming one "
            "would put an invented number in a field the calendar may display. "
            "Garmin may recompute this on upload -- unverified until the "
            "supervised live cycle."
        )
    return int(timed), basis


def _repeat_totals(step: Step) -> tuple[float, int]:
    """A repeat group's contribution: time multiplied by iterations.

    Distance steps are counted per iteration too, because the basis sentence
    reports how many of them contributed nothing, and a count that ignored the
    repeat would under-report what the estimate is missing.
    """
    iterations = step.iterations or 0
    timed = 0.0
    distance_steps = 0
    for child in step.repeat:
        if child.duration_secs is not None:
            timed += child.duration_secs
        elif child.distance_m is not None:
            distance_steps += 1
    return timed * iterations, distance_steps * iterations


def _flatten(workout_steps: list[Any], depth: int = 0) -> list[tuple[dict, int]]:
    """Every step in the tree, each with the nesting depth it was found at.

    Depth is carried because a repeat inside a repeat is refused, and the only
    way to see one is to know how deep you are.
    """
    out: list[tuple[dict, int]] = []
    for item in workout_steps:
        out.append((item, depth))
        if item.get("type") == "RepeatGroupDTO":
            out.extend(_flatten(item.get("workoutSteps") or [], depth + 1))
    return out


def validate_structure(structure: dict[str, Any]) -> None:
    """Refuse a structure that is malformed, naming what is wrong.

    The cases are the ones §8 (v1.5) names -- the shapes upstream accepts and
    should not -- plus two this project found by reading the builders:
    `create_repeat_group` writes the iteration count into both
    `numberOfIterations` and `endConditionValue`, which is two fields holding
    one fact and therefore two fields that can disagree; and the builders'
    default target is an explicit NO_TARGET, which is worth asserting rather
    than trusting, because v0's whole target story depends on it.

    Raises on the first problem rather than collecting them. A refusal the agent
    can act on is one sentence; a list of nine is a wall it will summarise.
    """
    if not structure.get("workoutName", "").strip():
        raise ComposeError("the structure has no workoutName.")

    segments = structure.get("workoutSegments") or []
    if not segments:
        raise ComposeError("the structure has no workoutSegments.")

    steps = segments[0].get("workoutSteps") or []
    if not steps:
        raise ComposeError("the structure has no steps.")

    flat = _flatten(steps)
    orders = [item.get("stepOrder") for item, _ in flat]

    if len(set(orders)) != len(orders):
        raise ComposeError(
            f"stepOrder is duplicated: {sorted(orders)}. Garmin orders a segment's "
            "steps by this field, so two steps sharing one have no defined order."
        )
    if sorted(orders) != list(range(1, len(orders) + 1)):
        raise ComposeError(
            f"stepOrder must be contiguous from 1, got {sorted(orders)}."
        )

    for item, depth in flat:
        if item.get("type") == "RepeatGroupDTO":
            if depth > 0:
                raise ComposeError(
                    "a repeat group is nested inside another repeat group. The model "
                    "allows it; Garmin's acceptance of it is unverified, so v0 "
                    "refuses it."
                )
            children = item.get("workoutSteps") or []
            if not children:
                raise ComposeError("a repeat group with no steps repeats nothing.")

            iterations = item.get("numberOfIterations")
            if iterations is None or iterations < 2:
                raise ComposeError(
                    f"a repeat group needs at least 2 iterations, not {iterations}."
                )
            end_value = item.get("endConditionValue")
            if end_value is not None and float(end_value) != float(iterations):
                raise ComposeError(
                    f"numberOfIterations ({iterations}) and endConditionValue "
                    f"({end_value}) disagree. They are two fields holding one fact."
                )
            continue

        target = item.get("targetType") or {}
        key = target.get("workoutTargetTypeKey")
        if key != NO_TARGET_KEY or target.get("workoutTargetTypeId") != NO_TARGET_ID:
            raise ComposeError(
                f"step {item.get('stepOrder')} carries target {key!r}. v0 composes "
                "no targets: every TargetType Garmin offers is zone-based, and this "
                "account's zones have never been fetched."
            )

        value = item.get("endConditionValue")
        if value is None:
            raise ComposeError(
                f"step {item.get('stepOrder')} has no endConditionValue, so nothing "
                "ends it."
            )
        _positive(float(value), f"step {item.get('stepOrder')} endConditionValue")
