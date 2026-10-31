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

**v1.6 allows one target: an explicit pace range on a running step.** v0
refused every target, and the reason given was that every `TargetType` Garmin
offers is zone-based and this account's zones have never been fetched. The
second half is still true; the first half was too broad. **An explicit pace
range is a pair of absolute speeds** -- it needs no zone, no heart-rate data and
no profile read -- so the refusal narrows rather than disappearing. Heart-rate
targets, pace *zones*, power, cadence and resistance stay refused, and so does
any target on a strength step.

**Garmin's side of a pace target is one constant, `PACE_CONTRACT`, and almost
all of it is unverified.** The target *id* is upstream's `TargetType.PACE_ZONE`;
the key string, the display order, the unit and which of the two values carries
the faster bound are community convention. They live in one place so that a
wrong one is wrong once (§9's risk row, accepted for one session).

**v1.6 adds a strength circuit, and one shape in it is ours rather than
upstream's.** A named exercise measured in *time* -- 40 seconds of Goblet Squat
-- is not a step the library builds: `create_strength_exercise_step` ends on
REPS and takes no duration, and `create_strength_rest_step` is timed but names
nothing. `_build_exercise` combines them, from the library's own enum members,
and **Garmin accepted the combination** on 2026-10-31 — workout 1746708075,
uploaded, scheduled, and **confirmed end to end on the watch**: each exercise
name, its duration and the rounds all show as planned. It was the open question
of v1.6, it is answered yes, and nothing about it is left unmeasured -- the
reps-based fallback is not needed. Every other strength piece is upstream's: the
`StrengthWorkout` model, the rest step, and the `category`/`exerciseName`
catalogue.

**What this module does not do is design the session.** It composes what it is
told. Which distance, how many reps, how long the recovery -- those need how the
legs feel and how much time there is, which no endpoint holds, so they belong in
the conversation (§1).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from garminconnect.exercises import EXERCISES
from garminconnect.workout import (
    ConditionType,
    ExecutableStep,
    RunningWorkout,
    StepType,
    StrengthWorkout,
    TargetType,
    WorkoutSegment,
    create_cooldown_step,
    create_distance_interval_step,
    create_interval_step,
    create_recovery_step,
    create_repeat_group,
    create_strength_rest_step,
    create_warmup_step,
)

from .errors import GarminSourceError

#: The sports this module composes, mapped to the library's workout model.
#: `StrengthWorkout` arrives in v1.6; `RunningWorkout` is v0's only sport and
#: stays the default, so nothing composed before this change composes
#: differently after it.
SPORT_MODELS = {
    "running": RunningWorkout,
    "strength": StrengthWorkout,
}

SPORTS = tuple(SPORT_MODELS)

#: A running session's time-measured kinds, each mapped to the builder that
#: makes it. Keyed rather than branched on, so the tool's enum is derived from
#: this dict and a new kind cannot arrive in the schema without a builder
#: behind it.
TIME_BUILDERS = {
    "warmup": create_warmup_step,
    "interval": create_interval_step,
    "recovery": create_recovery_step,
    "cooldown": create_cooldown_step,
}

#: A strength circuit's timed steps that carry no exercise name. The work step
#: is `exercise`, which is not here because it needs a name as well as a
#: duration and so does not fit the `builder(seconds, order)` shape --
#: `_build_exercise` makes that one.
#:
#: `warmup` and `cooldown` arrive after the first live strength session had to
#: drop them: v1.6 offered `exercise` and `rest` only, so a real 30-minute
#: session came out as a bare circuit and the human was told to warm up on
#: their own. The builders are upstream's and generic across sports, but
#: **Garmin's acceptance of a warmup step inside a `strength_training` workout
#: is unverified** -- the next live strength create settles it, and it is cheap
#: because that create was going to happen anyway.
STRENGTH_TIME_BUILDERS = {
    "warmup": create_warmup_step,
    "rest": create_strength_rest_step,
    "cooldown": create_cooldown_step,
}

TIME_BUILDERS_BY_SPORT = {
    "running": TIME_BUILDERS,
    "strength": STRENGTH_TIME_BUILDERS,
}

#: Only an interval may be measured in distance. A warmup or cooldown by
#: distance is expressible upstream and is left out of v0 on purpose: every kind
#: we offer is a kind the live cycle has to confirm, and the list should be as
#: short as the loop allows.
DISTANCE_BUILDERS = {
    "interval": create_distance_interval_step,
}

#: Nothing in a strength circuit is measured in distance. The empty dict is the
#: statement, and `_build_step` reads it rather than carrying a second rule
#: about which sports have distance steps.
DISTANCE_BUILDERS_BY_SPORT = {
    "running": DISTANCE_BUILDERS,
    "strength": {},
}

#: Which kinds each sport accepts. A single flat enum cannot express this --
#: `interval` means "run hard for 400m" and has no meaning in a circuit, while
#: `exercise` carries a name a running step has nowhere to put -- so the schema
#: offers the union and the refusal carries the per-sport rule.
STEP_KINDS_BY_SPORT = {
    "running": tuple(TIME_BUILDERS),
    "strength": ("exercise",) + tuple(STRENGTH_TIME_BUILDERS),
}

#: Every kind the composer has a builder for, in declaration order and without
#: duplicates. `server.py` derives its enum from this.
STEP_KINDS = tuple(
    dict.fromkeys(kind for kinds in STEP_KINDS_BY_SPORT.values() for kind in kinds)
)

#: Garmin's own id/key for "no target", which the builders already default to
#: when no target is passed (read from `garminconnect/workout.py`, not assumed).
#: Named here because `validate_structure` asserts it rather than trusting it.
NO_TARGET_KEY = "no.target"
NO_TARGET_ID = 1


@dataclass(frozen=True)
class PaceContract:
    """Garmin's side of a pace target: every part of it a claim, in one place.

    The **id** is the only part that is not a guess -- it is upstream's
    `TargetType.PACE_ZONE`, read rather than written as a literal. The key
    string, the display order, the unit and **which of the two values carries
    the faster bound** are community convention, and the library writes none of
    them: it writes `"no.target"` and nothing else.

    **[Mahi-verify]** by building one pace step in Connect and reading it back:
    the key string, the unit, the `displayOrder`, and which value Garmin expects
    first. Until that read-back all four are unverified. They are one constant
    rather than four literals at four call sites because the failure this guards
    is a swapped bound -- the watch alerting continuously, the easy end becoming
    the hard end -- and a wrong constant is wrong in one place.
    """

    target_key: str
    target_id: int
    display_order: int
    faster_bound_field: str
    slower_bound_field: str
    unit: str


PACE_CONTRACT = PaceContract(
    target_key="pace.zone",
    target_id=TargetType.PACE_ZONE,
    display_order=6,
    faster_bound_field="targetValueOne",
    slower_bound_field="targetValueTwo",
    unit="m/s",
)

#: The band a pace must fall inside, in seconds per kilometre. Faster than
#: 2:30/km is quicker than anybody has run a marathon and slower than 12:00/km
#: is slower than walking, so a value outside it is a typo or a unit mistake
#: rather than a session. Mahi set the band.
PACE_FASTEST_SECS_PER_KM = 150.0
PACE_SLOWEST_SECS_PER_KM = 720.0

#: `mm:ss`, two digits of seconds, no fraction. Strictness is the point: see
#: `parse_pace_min_km`.
_PACE_FORMAT = re.compile(r"^(\d{1,2}):(\d{2})$")


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
    `"repeat"` and neither measure is set. `exercise` belongs to an `exercise`
    step and nothing else; on any other kind it is refused rather than dropped,
    because a dropped name is one the watch silently will not show.

    `pace_min_km` is a two-element range, faster bound first, on a **running**
    step only. On a strength step and on a repeat group it is refused rather
    than dropped, for the same reason as `exercise`: Garmin stores a target per
    step, so a target put anywhere else disappears without complaint.
    """

    kind: str
    duration_secs: float | None = None
    distance_m: float | None = None
    iterations: int | None = None
    repeat: tuple["Step", ...] = field(default_factory=tuple)
    exercise: str | None = None
    pace_min_km: tuple[str, str] | None = None


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


def _format_pace(secs_per_km: float) -> str:
    """Seconds per kilometre as `m:ss`, which is the only unit a human sees.

    Rendering from the number rather than echoing the input string is what makes
    the preview and the payload two readings of one value. `"04:20"` comes back
    as `4:20` because the canonical form is the one the conversion produced.
    """
    whole = int(round(secs_per_km))
    return f"{whole // 60}:{whole % 60:02d}"


def parse_pace_min_km(text: Any) -> float:
    """Seconds per kilometre, from `mm:ss` as a human writes a pace.

    Strict on purpose, in both directions it could be loose. `"4:75"` parses
    arithmetically to 5:15 and is not what anybody meant; `"4:20.5"` is a
    precision no watch displays. Both are refused rather than normalised,
    because a pace quietly moved is a pace the human approved in words that no
    longer describe the workout.
    """
    if not isinstance(text, str):
        raise ComposeError(
            f"a pace is written as mm:ss per kilometre, not {text!r}."
        )
    match = _PACE_FORMAT.match(text.strip())
    if match is None:
        raise ComposeError(
            f"{text!r} is not a pace. Write it as mm:ss per kilometre -- "
            "'4:20' -- with two digits of seconds and no fractional part."
        )
    minutes, seconds = int(match.group(1)), int(match.group(2))
    if seconds >= 60:
        raise ComposeError(
            f"{text!r} has {seconds} seconds in it. A pace is mm:ss, so the "
            f"seconds must be under 60: as written it would mean "
            f"{_format_pace(minutes * 60 + seconds)}, which is not what it "
            "looks like."
        )
    total = float(minutes * 60 + seconds)
    if not PACE_FASTEST_SECS_PER_KM <= total <= PACE_SLOWEST_SECS_PER_KM:
        raise ComposeError(
            f"{text!r} is outside the pace band this server composes "
            f"({_format_pace(PACE_FASTEST_SECS_PER_KM)} to "
            f"{_format_pace(PACE_SLOWEST_SECS_PER_KM)} per km). A pace outside "
            "it is a typo or a unit mistake rather than a session."
        )
    return total


@dataclass(frozen=True)
class PaceTarget:
    """One pace range, converted once, read by everything that needs it.

    Holds both units because they have different audiences and must not be
    computed twice: the human approves **min/km** and the payload carries
    **m/s**. If the summary re-parsed the input string while the structure used
    the conversion, the two could disagree and the human would approve the right
    words over the wrong workout (Pass A.1).

    The m/s values are rounded at construction, which is **upstream of the
    approval marker** -- rounding later would make the preview's marker and the
    create's marker disagree over the same session (Pass B.1).
    """

    faster_secs_per_km: float
    slower_secs_per_km: float
    faster_m_s: float
    slower_m_s: float

    @property
    def midpoint_secs_per_km(self) -> float:
        """The middle of the range, **in seconds per km rather than in m/s.**

        The two are not the same number: averaging the paces and averaging the
        speeds differ by about a third of a second per km over a 20-second
        range, because one is the harmonic mean of the other. The pace is the
        unit the human approved, so the midpoint is taken there -- 4:20 to 4:40
        gives 4:30, which is the answer anybody would give out loud, and that is
        the test a derived number has to pass before it goes in a displayed
        field.
        """
        return (self.faster_secs_per_km + self.slower_secs_per_km) / 2.0

    @property
    def midpoint_rendered(self) -> str:
        """The midpoint in min/km, for the basis sentence to name."""
        return f"{_format_pace(self.midpoint_secs_per_km)}/km"

    def seconds_for(self, distance_m: float) -> float:
        """How long `distance_m` takes at the midpoint of this range.

        **Arithmetic on the prescription, not a measurement.** It says what the
        session takes if it is run as instructed, which is the only sense in
        which this server ever knows a duration for a distance step.
        """
        return distance_m / 1000.0 * self.midpoint_secs_per_km

    @property
    def rendered(self) -> str:
        """The range in min/km. A human is never shown m/s."""
        return (
            f"{_format_pace(self.faster_secs_per_km)}-"
            f"{_format_pace(self.slower_secs_per_km)}/km"
        )

    def target_block(self) -> dict[str, Any]:
        """Garmin's `targetType` block, entirely from `PACE_CONTRACT`."""
        return {
            "workoutTargetTypeId": PACE_CONTRACT.target_id,
            "workoutTargetTypeKey": PACE_CONTRACT.target_key,
            "displayOrder": PACE_CONTRACT.display_order,
        }

    def target_values(self) -> dict[str, float]:
        """The two speeds, keyed by the fields `PACE_CONTRACT` names.

        `ExecutableStep` declares neither field, and its `extra="allow"` is what
        lets them through -- the same mechanism `_build_exercise` uses, and the
        only part of a pace step that is not upstream's.
        """
        return {
            PACE_CONTRACT.faster_bound_field: self.faster_m_s,
            PACE_CONTRACT.slower_bound_field: self.slower_m_s,
        }


def pace_target(bounds: Any) -> PaceTarget:
    """A pace range, validated and converted -- the one conversion in the path.

    Refuses an inverted range rather than sorting it. Sorting would be kinder to
    a caller that typed the bounds backwards and would hide a caller that has
    understood the field backwards, which is the one of the two worth finding.
    """
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
        raise ComposeError(
            "a pace target is a range of two paces in min/km, faster bound "
            "first -- ['4:20', '4:40']. A single pace is not a range."
        )

    faster = parse_pace_min_km(bounds[0])
    slower = parse_pace_min_km(bounds[1])

    if faster == slower:
        raise ComposeError(
            f"a pace range needs two different bounds, not "
            f"{_format_pace(faster)} twice. A zero-width range is not a range, "
            "and what Garmin does with one has never been measured."
        )
    if faster > slower:
        raise ComposeError(
            f"a pace range is written faster bound first: "
            f"['{_format_pace(slower)}', '{_format_pace(faster)}'], not "
            f"['{_format_pace(faster)}', '{_format_pace(slower)}']. It is "
            "refused rather than sorted, because sorting would hide a caller "
            "that has the two fields the wrong way round."
        )

    return PaceTarget(
        faster_secs_per_km=faster,
        slower_secs_per_km=slower,
        faster_m_s=round(1000.0 / faster, 3),
        slower_m_s=round(1000.0 / slower, 3),
    )


#: How many candidates a refusal lists before it says "and N more". A refusal
#: the agent can act on is a short list; forty names is a wall it will
#: summarise, which is the §5 failure this repo keeps paying for.
_MAX_CANDIDATES = 8


def resolve_exercise(term: str | None) -> dict[str, str]:
    """Garmin's catalogue entry for a named exercise, or a refusal.

    Garmin does not store an exercise as free text. It stores a `category` and
    an `exerciseName`, both drawn from a fixed catalogue of 1527 entries, so a
    name the catalogue does not hold cannot be sent at all.

    Two passes, exact before substring, because the two disagree and the exact
    one is right: "Squat" is the display name of one exercise *and* a substring
    of 125 others, so substring-first would refuse the very name a human is most
    likely to type. The exact pass is case-insensitive -- "goblet squat" is the
    same request as "Goblet Squat", and making a human match Garmin's
    capitalisation is a refusal that teaches nothing.

    **An ambiguous term is refused, never resolved to the nearest match.** The
    failure directions are not symmetric: refusing costs one more turn in the
    conversation, while guessing puts a different exercise on the watch than the
    one that was approved, under a name that reads correct in the preview.
    """
    wanted = (term or "").strip()
    if not wanted:
        raise ComposeError(
            "an exercise step needs an exercise name -- Garmin stores one per "
            "step and has no unnamed exercise."
        )

    lowered = wanted.lower()

    exact = [entry for entry in EXERCISES if entry["name"].lower() == lowered]
    if len(exact) == 1:
        return exact[0]

    matches = [entry for entry in EXERCISES if lowered in entry["name"].lower()]
    if len(matches) == 1:
        return matches[0]

    if not matches:
        raise ComposeError(
            f"no Garmin exercise is named {wanted!r}. Garmin stores an exercise as "
            "a category and a name from its own catalogue, so a name it does not "
            "hold cannot be written. Search the catalogue for a broader word -- "
            "'squat', 'press', 'plank', 'row' -- rather than guessing again."
        )

    shown = ", ".join(repr(entry["name"]) for entry in matches[:_MAX_CANDIDATES])
    extra = len(matches) - _MAX_CANDIDATES
    more = f", and {extra} more" if extra > 0 else ""
    raise ComposeError(
        f"{wanted!r} matches {len(matches)} Garmin exercises: {shown}{more}. Name "
        "one exactly, or search the catalogue to see them all. Picking the "
        "nearest would put a different exercise on the watch than the one that "
        "was approved."
    )


#: How many matches a search returns unless asked for more. Enough to show a
#: category's worth of variants, short enough that an agent reads it rather
#: than summarising it -- the same reasoning as `_MAX_CANDIDATES`, one step up.
DEFAULT_SEARCH_LIMIT = 25


def search_exercises(
    term: str, limit: int = DEFAULT_SEARCH_LIMIT
) -> tuple[tuple[dict[str, str], ...], int]:
    """Catalogue entries matching every word in `term`, best first, and the total.

    Discovery, not commitment. `resolve_exercise` refuses an ambiguous term
    because committing to a guess puts the wrong exercise on a watch -- and
    until now there was no way to resolve that ambiguity except to guess again.
    The first live session spent three turns on it. This is the looking, at the
    cost of **zero Garmin requests**: the catalogue is a constant in the
    library, not an endpoint.

    **Every name this returns resolves unambiguously.** It returns catalogue
    display names, and `resolve_exercise`'s exact pass matches one of those by
    definition -- so a name read out of a search can be handed straight back to
    a step. That property is the whole reason the search is worth having, and
    it is asserted rather than assumed.

    All words must appear, in any order, so "squat goblet" finds what "goblet
    squat" finds. Deliberately more permissive than `resolve_exercise`: looking
    should be easy and committing should be strict.

    Returns the total beside the capped list. A truncated list that does not say
    it is truncated is the silent partial this project refuses everywhere else
    -- it reads as "these are the only ones", which is how a human ends up
    believing an exercise does not exist.
    """
    words = [word for word in (term or "").lower().split() if word]
    if not words:
        raise ComposeError(
            "a search needs something to search for. Try a movement word -- "
            "'squat', 'lunge', 'press', 'plank'."
        )
    if limit < 1:
        raise ComposeError(f"limit must be at least 1, not {limit}.")

    matches = [
        entry
        for entry in EXERCISES
        if all(word in entry["name"].lower() for word in words)
    ]

    needle = " ".join(words)

    def rank(entry: dict[str, str]) -> tuple[int, str]:
        """Exact name, then names that start with the term, then the rest.

        Alphabetical within each band, so the order does not depend on the
        catalogue's own ordering and the same search twice reads the same.
        """
        name = entry["name"].lower()
        if name == needle:
            return (0, name)
        if name.startswith(needle):
            return (1, name)
        return (2, name)

    matches.sort(key=rank)
    return tuple(matches[:limit]), len(matches)


def _build_exercise(step: Step, order: int) -> Any:
    """A named exercise held for a number of seconds.

    **This step shape is ours, not the library's.**
    `create_strength_exercise_step` ends on REPS and takes no duration;
    `create_strength_rest_step` is timed but carries no name. A named exercise
    measured in *time* is the two combined, which upstream does not offer -- so
    it is assembled here through `ExecutableStep`'s `extra="allow"`, from the
    same enum members the library's own builders use rather than from literals
    of ours.

    **Garmin accepts it, and the watch shows it.** CONFIRMED end to end by the
    first live create, 2026-10-31: workout 1746708075, uploaded and scheduled
    with no warnings, and the watch displays each exercise name, its duration
    and the rounds as planned. Every field was one upstream writes somewhere and
    the combination was ours; the combination is now measured rather than hoped,
    and the reps-based fallback is not needed. (docs/SCOPE.md §5, v1.6.)
    """
    if step.distance_m is not None:
        raise ComposeError(
            f"step {order}: an exercise is measured in time, not distance. "
            "Give it duration_secs."
        )
    if step.duration_secs is None:
        raise ComposeError(
            f"step {order}: an exercise step needs duration_secs -- how long to "
            "hold it for."
        )
    _positive(step.duration_secs, f"step {order} (exercise) duration_secs")

    entry = resolve_exercise(step.exercise)

    return ExecutableStep(
        stepOrder=order,
        stepType={
            "stepTypeId": StepType.INTERVAL,
            "stepTypeKey": "interval",
            "displayOrder": 3,
        },
        endCondition={
            "conditionTypeId": ConditionType.TIME,
            "conditionTypeKey": "time",
            "displayOrder": 2,
            "displayable": True,
        },
        endConditionValue=float(step.duration_secs),
        targetType={
            "workoutTargetTypeId": TargetType.NO_TARGET,
            "workoutTargetTypeKey": NO_TARGET_KEY,
            "displayOrder": 1,
        },
        category=entry["category"],
        exerciseName=entry["exercise"],
    )


def _with_target(built: Any, target_values: dict[str, float]) -> Any:
    """A built step, plus the two target values if there are any.

    Its callers pass the target block as a third argument **only when there is
    one**, which is not fussiness: `create_strength_rest_step` takes two
    arguments and has no `target_type` parameter at all, so handing it a `None`
    third argument is a `TypeError`. §15's 3.b read the five *running* builders
    and generalised to "every upstream builder"; the strength rest step is the
    counter-example, and it is a second reason the per-sport refusal has to come
    first -- by the time a builder is chosen, a pace in a strength session is
    already a crash rather than a wrong workout.

    `targetValueOne` and `targetValueTwo` are not parameters of any upstream
    builder and are not declared on `ExecutableStep`; they arrive through its
    `extra="allow"`, which `model_copy` carries into `model_dump`. Measured, not
    assumed -- and the empty case returns the step untouched, so a session with
    no targets composes byte-for-byte what it composed before pace existed.
    """
    if not target_values:
        return built
    return built.model_copy(update=target_values)


def _build_step(step: Step, order: int, sport: str) -> Any:
    """One step, built by the builder its shape names.

    `stepOrder` is assigned by the caller's counter and never taken from the
    step: a duplicated `stepOrder` is one of the malformed shapes §8 asks us to
    refuse, and a value the caller cannot supply is a value the caller cannot
    duplicate. `validate_structure` still checks it, for a structure that
    arrives from somewhere else.
    """
    if step.kind == "repeat":
        raise ComposeError("a repeat group cannot be nested inside another repeat group.")

    kinds = STEP_KINDS_BY_SPORT[sport]
    if step.kind not in kinds:
        raise ComposeError(
            f"step {order}: {step.kind!r} is not a step kind for a {sport} "
            f"session. Valid kinds: {', '.join(kinds)}."
        )

    if step.duration_secs is not None and step.distance_m is not None:
        raise ComposeError(
            f"step {order} ({step.kind}) sets both duration_secs and distance_m. "
            "A step ends one way or the other, never both."
        )

    # Checked here, above the `exercise` return, because a pace on an exercise
    # step would otherwise be dropped rather than refused -- the failure this
    # project keeps paying for.
    target_block: dict[str, Any] | None = None
    target_values: dict[str, float] = {}
    if step.pace_min_km is not None:
        if sport != "running":
            raise ComposeError(
                f"step {order} ({step.kind}): a pace target is for a running "
                f"step, and this is a {sport} session, which is prescribed by "
                "time. This is refused by sport rather than being impossible to "
                f"build: a {sport} warmup is made by running's own builder, "
                "which does accept a target."
            )
        target = pace_target(step.pace_min_km)
        target_block = target.target_block()
        target_values = target.target_values()

    if step.kind == "exercise":
        return _build_exercise(step, order)

    if step.exercise is not None:
        raise ComposeError(
            f"step {order} ({step.kind}) carries an exercise name. Only an "
            "'exercise' step has anywhere to put one -- on any other kind Garmin "
            "would drop it, and the watch would show a step with no name."
        )

    if step.duration_secs is not None:
        builder = TIME_BUILDERS_BY_SPORT[sport].get(step.kind)
        if builder is None:
            raise ComposeError(
                f"unknown step kind {step.kind!r}. Valid kinds: {', '.join(kinds)}."
            )
        _positive(step.duration_secs, f"step {order} ({step.kind}) duration_secs")
        if target_block is None:
            return builder(step.duration_secs, order)
        return _with_target(builder(step.duration_secs, order, target_block), target_values)

    if step.distance_m is not None:
        distance_builders = DISTANCE_BUILDERS_BY_SPORT[sport]
        if not distance_builders:
            raise ComposeError(
                f"step {order}: a {sport} step is measured in time, not distance. "
                "Give it duration_secs instead."
            )
        builder = distance_builders.get(step.kind)
        if builder is None:
            raise ComposeError(
                f"step {order}: only an {', '.join(distance_builders)} step may be "
                f"measured in distance, not a {step.kind!r}. Give it duration_secs "
                "instead."
            )
        _positive(step.distance_m, f"step {order} ({step.kind}) distance_m")
        if target_block is None:
            return builder(step.distance_m, order)
        return _with_target(builder(step.distance_m, order, target_block), target_values)

    raise ComposeError(
        f"step {order} ({step.kind}) has neither duration_secs nor distance_m. "
        "A step needs an end condition."
    )


def _build_repeat(step: Step, order: int, sport: str) -> tuple[Any, int]:
    """A repeat group, and the next free `stepOrder` after it.

    The children share the parent's numbering -- Garmin's `stepOrder` is flat
    across the segment, not per-group -- so this returns the counter it consumed
    rather than letting the caller guess.
    """
    if step.pace_min_km is not None:
        raise ComposeError(
            f"step {order}: a repeat group carries no target of its own. Put the "
            "pace on the steps inside it -- Garmin stores a target per step, so "
            "a pace on the group would vanish without being refused."
        )
    if step.iterations is None:
        raise ComposeError(f"step {order}: a repeat group needs iterations.")
    if step.iterations < 2:
        raise ComposeError(
            f"step {order}: a repeat group needs at least 2 iterations, not "
            f"{step.iterations}. One iteration is not a repeat -- inline the step."
        )
    if not step.repeat:
        raise ComposeError(
            f"step {order}: a repeat group with no steps repeats nothing. The "
            "nested steps go in the group's `repeat` field."
        )

    next_order = order + 1
    children = []
    for child in step.repeat:
        children.append(_build_step(child, next_order, sport))
        next_order += 1

    return create_repeat_group(step.iterations, children, order), next_order


def _sport_type(sport: str) -> dict[str, Any]:
    """Garmin's sportType block for a sport, read off the library's own model.

    The segment repeats the workout's sport type, so both copies are taken from
    one call of this and cannot disagree -- and taking it from the model's own
    default means an upstream change surfaces here instead of being silently
    contradicted by a literal of ours. The previous version of this function was
    two hand-written copies of the running block.
    """
    factory = SPORT_MODELS[sport].model_fields["sportType"].default_factory
    return dict(factory())


def compose(
    name: str,
    steps: tuple[Step, ...],
    description: str | None = None,
    sport: str = "running",
) -> dict[str, Any]:
    """The structure Garmin's upload wants, built from a step list.

    Validates its own output before returning it. That looks redundant -- we
    just built it -- and it is not: the builders are upstream code, and a change
    in them is something our tests should catch rather than Garmin. It is also
    exactly the check `create` will run on an approved structure, so the rule
    lives in one place from the start.

    `sport` defaults to running, which is what v0 composed, so every caller
    written before strength existed composes the same structure after it.
    """
    if sport not in SPORT_MODELS:
        raise ComposeError(
            f"unknown sport {sport!r}. Valid sports: {', '.join(SPORTS)}."
        )
    if not name or not name.strip():
        raise ComposeError("a workout needs a name.")
    if not steps:
        raise ComposeError("a workout with no steps is not a session.")

    built: list[Any] = []
    order = 1
    for step in steps:
        if step.kind == "repeat":
            group, order = _build_repeat(step, order, sport)
            built.append(group)
        else:
            built.append(_build_step(step, order, sport))
            order += 1

    estimated, _ = estimate_duration(steps)

    workout = SPORT_MODELS[sport](
        workoutName=name.strip(),
        sportType=_sport_type(sport),
        estimatedDurationInSecs=estimated,
        description=description,
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType=_sport_type(sport),
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

    **A distance step is estimated only when it was given a pace** (option (b),
    ratified 2026-10-31). Then the duration is arithmetic on the range the human
    approved, taken at its midpoint: it says what the session takes if it is run
    as instructed. Without a pace the step still contributes **zero**, because
    this account has no measured pace -- `get_heart_rate_zones` and the pace
    endpoints have never been run -- and inventing one would put a fabricated
    number in a field the calendar displays.

    The distinction is the whole decision. A prescribed pace is not a
    measurement of the athlete, but multiplying it out is not a claim about the
    athlete either; it is a claim about the instruction, and the instruction is
    the thing the human just approved. Option (a) -- change the sentence, leave
    the arithmetic -- was ratified first and reversed the same day, when the
    live check below removed the assumption it rested on.

    **Garmin does not recompute it on upload. MEASURED 2026-10-31** --
    workout 1746800115 ("6x300m", scheduled 1825752591): the server computed
    27:30, and Connect displays 27:30 unchanged. So this number is not a
    provisional one that Garmin corrects; **it is the number the calendar
    shows, and nothing downstream fixes it.** That is what reversed the
    decision: under (a) a paced session would have displayed several minutes
    short, permanently, and **the basis sentence explaining it does not travel
    to Connect** -- the calendar shows the bare number with nothing beside it.

    An all-distance session with no paces still estimates 0, which reads as
    broken unless the basis is read with it. The two are returned together for
    exactly that reason, and `estimated_duration_secs` is never surfaced without
    its basis.
    """
    total, paced_distance, unpaced_distance, paced_timed = _tally(steps)

    parts = ["Sum of the time-based steps."]

    if paced_distance:
        parts.append(
            f"{paced_distance} distance step(s) are estimated from the pace "
            "range each was given, at its midpoint: arithmetic on the "
            "prescription the human approved, not a measured pace."
        )
    if unpaced_distance:
        parts.append(
            f"{unpaced_distance} distance step(s) contribute 0: they carry no "
            "pace, this account has no measured pace, and assuming one would "
            "put an invented number in a field the calendar displays."
        )
    if paced_timed and not paced_distance:
        parts.append(
            "A pace target on a time-based step is not used to estimate "
            "anything -- that step's duration is already known."
        )
    if paced_distance or unpaced_distance:
        parts.append(
            "Garmin does not recompute it on upload (measured 2026-10-31), so "
            "this is the number the calendar will show."
        )

    return int(total), " ".join(parts)


def _tally(
    steps: tuple[Step, ...], iterations: int = 1
) -> tuple[float, int, int, int]:
    """Seconds, and how many steps of each kind the basis has to account for.

    Counts are **per iteration of the session as run**, not per written step, so
    a repeat group's children count once for each round. The basis reports what
    the athlete will actually do, and four 800s estimated from a pace is four
    facts about the session rather than one about the source.
    """
    total = 0.0
    paced_distance = 0
    unpaced_distance = 0
    paced_timed = 0

    for step in steps:
        if step.kind == "repeat":
            child_total, child_paced, child_unpaced, child_timed = _tally(
                step.repeat, step.iterations or 0
            )
            total += child_total
            paced_distance += child_paced
            unpaced_distance += child_unpaced
            paced_timed += child_timed
        elif step.duration_secs is not None:
            total += step.duration_secs * iterations
            if step.pace_min_km is not None:
                paced_timed += iterations
        elif step.distance_m is not None:
            if step.pace_min_km is not None:
                target = pace_target(step.pace_min_km)
                total += target.seconds_for(step.distance_m) * iterations
                paced_distance += iterations
            else:
                unpaced_distance += iterations

    return total, paced_distance, unpaced_distance, paced_timed


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

    sport_key = (structure.get("sportType") or {}).get("sportTypeKey")

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

        step_key = (item.get("stepType") or {}).get("stepTypeKey")
        is_strength_work = sport_key == "strength_training" and step_key == "interval"
        if is_strength_work and not item.get("category"):
            raise ComposeError(
                f"step {item.get('stepOrder')} of a strength session is a work step "
                "carrying no exercise category. Garmin identifies an exercise by "
                "category and name, so a step without one shows on the watch as an "
                "unnamed block -- which is the one thing a strength session is for. "
                "A rest step is exempt: it names no exercise by design."
            )

        # The relaxation is per sport AND per target type. A global loosening
        # would re-permit a target on a strength step, which is the refusal the
        # whole strength path depends on arriving by the back door (§15 3.a).
        target = item.get("targetType") or {}
        key = target.get("workoutTargetTypeKey")
        target_id = target.get("workoutTargetTypeId")
        faster = item.get(PACE_CONTRACT.faster_bound_field)
        slower = item.get(PACE_CONTRACT.slower_bound_field)
        where = f"step {item.get('stepOrder')}"

        if key == NO_TARGET_KEY and target_id == NO_TARGET_ID:
            if faster is not None or slower is not None:
                raise ComposeError(
                    f"{where} carries no target type but holds "
                    f"{PACE_CONTRACT.faster_bound_field}/"
                    f"{PACE_CONTRACT.slower_bound_field}. A target value with no "
                    "target type is a number the watch has no rule for."
                )
        elif key == PACE_CONTRACT.target_key and target_id == PACE_CONTRACT.target_id:
            if sport_key != _sport_type("running")["sportTypeKey"]:
                raise ComposeError(
                    f"{where} carries a pace target in a {sport_key!r} session. "
                    "Pace targets are running only (§5, v1.6); every other sport "
                    "is prescribed by time."
                )
            if faster is None or slower is None:
                raise ComposeError(
                    f"{where} carries a pace target with only one bound. A pace "
                    f"range needs both {PACE_CONTRACT.faster_bound_field} and "
                    f"{PACE_CONTRACT.slower_bound_field}."
                )
            _positive(float(faster), f"{where} {PACE_CONTRACT.faster_bound_field}")
            _positive(float(slower), f"{where} {PACE_CONTRACT.slower_bound_field}")
            if float(faster) <= float(slower):
                raise ComposeError(
                    f"{where}: {PACE_CONTRACT.faster_bound_field} carries the "
                    f"faster bound, so in {PACE_CONTRACT.unit} it must be the "
                    f"larger number -- got {faster} against {slower}."
                )
        else:
            raise ComposeError(
                f"{where} carries target {key!r}. v1.6 composes an explicit pace "
                "range on a running step and nothing else: every other TargetType "
                "Garmin offers is zone-based, and this account's zones have never "
                "been fetched."
            )

        value = item.get("endConditionValue")
        if value is None:
            raise ComposeError(
                f"step {item.get('stepOrder')} has no endConditionValue, so nothing "
                "ends it."
            )
        _positive(float(value), f"step {item.get('stepOrder')} endConditionValue")
