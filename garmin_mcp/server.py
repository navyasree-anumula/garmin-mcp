"""The MCP tool layer.

This module must NEVER import `garminconnect` (docs/SCOPE.md §11). It talks to
`garmin_mcp.source` and nothing else.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__
from .source import client as source
from .source import compose as source_compose
from .source import metrics as metric_map
from .source import write as source_write
from .source import readings as source_readings
from .source.errors import GarminSourceError

logger = logging.getLogger(__name__)


def _server_version() -> str:
    """Which build is actually answering.

    `docker run` does not re-pull a moving tag, so a config pinned to `latest`
    silently keeps running whatever was pulled last. Without this, "am I on the
    current image?" is unanswerable from inside a conversation. GARMIN_MCP_BUILD
    is stamped by CI with the commit; it is absent on a local build.
    """
    build = os.environ.get("GARMIN_MCP_BUILD", "").strip()
    return f"{__version__}+{build}" if build else __version__


# Passed to the SDK so the build also appears in `serverInfo` on the initialize
# handshake, not only inside a tool result. That is the first thing a client
# shows and the only thing visible without calling anything -- it was empty, so
# a client could display the server as connected while saying nothing about
# which build had connected. Exactly the gap `_server_version` exists to close,
# left open at the one place it is cheapest to read.
mcp = MCPServer("garmin", version=_server_version())


class AuthStatus(BaseModel):
    """Wire model. Every field is named so a reader knows what it means without
    guessing -- including the unit system, because a bare number elsewhere in
    this server would be ambiguous (docs/SCOPE.md §5)."""

    authenticated: bool = Field(description="True only if Garmin accepted the stored tokens just now.")
    display_name: str = Field(description="Garmin Connect display name for the account.")
    full_name: str = Field(description="Full name on the account, may be empty.")
    unit_system: str = Field(description="Account measurement system, e.g. 'metric' or 'statute'.")
    tokens_path: str = Field(description="Path to the token file inside the container.")
    tokens_modified_utc: str = Field(description="When the token file was last written, ISO-8601 UTC.")
    server_version: str = Field(description="Version of this MCP server, plus the build commit when CI-built.")
    writes_enabled: bool = Field(
        description=(
            "Whether this server will write to Garmin at all. False means every "
            "write is refused before any request, and only the read tools and "
            "the workout preview can do anything."
        )
    )


@mcp.tool(
    title="Garmin authentication status",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
def garmin_auth_status() -> AuthStatus:
    """Check whether this server can still talk to Garmin Connect.

    Makes a live authenticated call, so a success proves the stored tokens work
    right now. Use this first when other Garmin tools start failing.

    Also reports whether writes are enabled. Ask this before composing a session
    you intend to send: a refusal after the human has approved a structure wastes
    their decision, and the answer costs nothing beyond this call.
    """
    try:
        status = source.auth_status()
    except GarminSourceError as exc:
        # str(exc) carries the actionable message; the agent should surface it
        # rather than retrying.
        raise ToolError(str(exc)) from exc

    return AuthStatus(
        authenticated=status.authenticated,
        display_name=status.display_name,
        full_name=status.full_name,
        unit_system=status.unit_system,
        tokens_path=status.tokens_path,
        tokens_modified_utc=status.tokens_modified_utc,
        server_version=_server_version(),
        writes_enabled=source_write.writes_enabled(),
    )


# --------------------------------------------------------------------------
# Training metrics (docs/SCOPE.md §5, v1.5)
# --------------------------------------------------------------------------

#: The agent's vocabulary, derived from the map rather than restated here.
#: Writing the keys out again would put the same list in two places, which is
#: the drift pass A exists to find -- and the tool's JSON schema carries this as
#: an enum, so a wrong key is refused by the client before a request is made.
MetricKey = Literal[tuple(metric_map.keys_for_group("training"))]  # type: ignore[valid-type]
HealthMetricKey = Literal[tuple(metric_map.keys_for_group("health"))]  # type: ignore[valid-type]


def _metrics_help(group: str, closing: str) -> str:
    """Build the parameter description from the map, units included.

    §5 (v1.5): the enum is how the model learns what exists, so each value
    carries its unit. A phrase is Garmin's own wording for a code we have no
    mapping for, and is marked as such rather than given a fake unit.

    Takes the group rather than hard-coding one, so a second metric tool cannot
    describe itself from a list that has drifted from the map.
    """
    rows = []
    for m in metric_map.METRICS:
        if m.group != group:
            continue
        # A no-source row is neither a number nor a phrase: it is a metric this
        # watch cannot answer, kept in the map on purpose (§5). Labelling it
        # `phrase` would invite the agent to expect a word back.
        if m.source_method is None:
            label = "unsupported on this watch"
        else:
            label = m.unit or "phrase"
        rows.append(f"{m.key} [{label}]")
    return (
        f"Which {group} metrics to read, as a list. " + ", ".join(rows) + ". " + closing
    )


_TRAINING_CLOSING = (
    "Ask for everything you need in ONE call: metrics that share a source cost "
    "a single Garmin request together, and three of these come from one payload."
)

_HEALTH_CLOSING = (
    "Ask for everything you need in ONE call: all four HRV metrics come from "
    "one payload, so together they cost a single Garmin request. Ask for "
    "`hrv_status` and `hrv_feedback` TOGETHER -- the first is the verdict, the "
    "second is the key Garmin's app renders a sentence from. `spo2` is "
    "answerable and will tell you this watch does not measure it -- which is a "
    "better answer than silence."
)


class MetricReading(BaseModel):
    """One metric's answer, self-describing (docs/SCOPE.md §5).

    Every field exists because a reader would otherwise have to guess: a bare
    number has no unit, two sources for one metric can disagree, and "nothing
    came back" has four different meanings that need four different responses.
    """

    metric: str = Field(description="The metric key that was asked for.")
    value: float | int | str | None = Field(
        description="The reading, or null when the verdict is not 'present'."
    )
    unit: str = Field(
        description=(
            "Unit of `value`. Empty for a phrase. Where a unit is inferred "
            "rather than stated by Garmin, it says so."
        )
    )
    verdict: str = Field(
        description=(
            "present = there is a reading. no_data = a well-formed empty answer, "
            "which says nothing about capability (a day the watch was not worn "
            "looks like this). not_matured = empty for a metric that needs "
            "accumulated wear. unsupported = this account or device cannot "
            "answer at all. error = OUR call failed, not Garmin's data."
        )
    )
    source: str = Field(
        description="The garminconnect method this value actually came from."
    )
    measured_on: str = Field(description="The date this reading is for.")
    note: str = Field(
        default="", description="Why there is no reading, or what is unconfirmed."
    )


class TrainingReadings(BaseModel):
    date: str = Field(description="The date the readings are for.")
    date_basis: str = Field(
        description="What that date means, since Garmin's own dates are device-local."
    )
    readings: list[MetricReading]


DATE_BASIS = (
    "UTC calendar date. Garmin's dates are device-local, so a run near midnight "
    "in another timezone can land on the adjacent day."
)


def _read_group(
    group: str, keys: list[str], date: str | None
) -> tuple[str, list[MetricReading]]:
    """Guard the keys against their group, then run the seam.

    Checked here rather than relying on the schema's enum. The enum stops a
    compliant client, and this stops everything else -- including a health
    metric asked of the training tool, which `metric_map.BY_KEY` would happily
    resolve. Mirrors `probe --explain`: name the valid keys rather than just
    refusing, because a "no" that does not say what would work costs the agent
    another round trip to find out.

    Shared by both metric tools rather than copied into each: a second copy of
    a guard is how one tool ends up accepting a key the other refuses.
    """
    allowed = metric_map.keys_for_group(group)
    unknown = [key for key in keys if key not in allowed]
    if unknown or not keys:
        raise ToolError(
            f"unknown or empty metrics {unknown!r}. Valid metrics: {', '.join(allowed)}"
        )

    try:
        measured_on, readings = source_readings.metrics_for(keys, date)
    except GarminSourceError as exc:
        raise ToolError(str(exc)) from exc

    return measured_on, [
        MetricReading(
            metric=r.metric,
            value=r.value,
            unit=r.unit,
            verdict=r.verdict,
            source=r.source,
            measured_on=r.measured_on,
            note=r.note,
        )
        for r in readings
    ]


@mcp.tool(
    title="Garmin training metrics",
    annotations=ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True
    ),
)
def garmin_training(
    metrics: Annotated[
        list[MetricKey],
        Field(description=_metrics_help("training", _TRAINING_CLOSING)),
    ],
    date: Annotated[
        str | None,
        Field(description="YYYY-MM-DD. Defaults to today's UTC calendar date."),
    ] = None,
) -> TrainingReadings:
    """Read Garmin's own training measurements for a date.

    Returns measurements, not advice: Garmin's phrase where Garmin has one, the
    number and its unit where it has that, and an explicit reason where it has
    neither. What the numbers mean for a given week is a judgement that needs
    things no endpoint holds -- how the legs feel, how much time there is, what
    hurts -- so it belongs in the conversation, not here (docs/SCOPE.md §1).

    Ask for several metrics at once. `training_status`, `load_balance` and
    `vo2_max` all come out of one payload, so together they cost one request.
    """
    measured_on, readings = _read_group("training", list(metrics), date)
    return TrainingReadings(
        date=measured_on, date_basis=DATE_BASIS, readings=readings
    )


# --------------------------------------------------------------------------
# Health metrics (docs/SCOPE.md §5, v1.5)
# --------------------------------------------------------------------------


class HealthReadings(BaseModel):
    date: str = Field(description="The date the readings are for.")
    date_basis: str = Field(
        description="What that date means, since Garmin's own dates are device-local."
    )
    readings: list[MetricReading]


@mcp.tool(
    title="Garmin health metrics",
    annotations=ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True
    ),
)
def garmin_health(
    metrics: Annotated[
        list[HealthMetricKey],
        Field(description=_metrics_help("health", _HEALTH_CLOSING)),
    ],
    date: Annotated[
        str | None,
        Field(description="YYYY-MM-DD. Defaults to today's UTC calendar date."),
    ] = None,
) -> HealthReadings:
    """Read Garmin's own overnight and resting measurements for a date.

    Returns measurements, not advice. Whether a low HRV means back off or means
    the watch slipped is a judgement that needs things no endpoint holds -- how
    the legs feel, what else happened that night -- so it belongs in the
    conversation, not here (docs/SCOPE.md §1).

    All four HRV metrics come out of one payload, so asking for every one of
    them costs a single request. Ask for `hrv_weekly` alongside
    `hrv_last_night` rather than instead of it: a single night against no
    baseline is close to unreadable.

    **`hrv_status` is a verdict; `hrv_feedback` is a message key.**
    `hrv_status` classifies HRV against the athlete's own baseline band, so a
    night above their average can still read `UNBALANCED` when it sits outside
    that band. `hrv_feedback` returns an identifier like `HRV_UNBALANCED_11` --
    the key Garmin's own app renders a sentence from. The trailing number
    selects one of several messages, and what any given number says is NOT
    known here, so do not guess at it; the Connect app is where that sentence
    can be read.

    Whether the two can ever disagree is **unmeasured** -- on 2026-10-26 they
    agreed. Taking both still costs one request, so there is no reason not to,
    and the key carries detail the verdict does not.

    **An empty answer is not an incapable watch.** A night the watch was not
    worn returns `no_data`, and `unsupported` means this account or device
    cannot answer at all -- `spo2` is the standing example, measured as
    unsupported on this watch rather than quietly dropped.
    """
    measured_on, readings = _read_group("health", list(metrics), date)
    return HealthReadings(
        date=measured_on, date_basis=DATE_BASIS, readings=readings
    )


# --------------------------------------------------------------------------
# Workouts -- preview only (docs/SCOPE.md §5, v1.5)
# --------------------------------------------------------------------------

#: Every step kind the composer has a builder for, plus the repeat group. Built
#: from `compose` rather than restated, so a kind cannot appear in the schema
#: without something behind it to build it.
StepKind = Literal[tuple(source_compose.STEP_KINDS + ("repeat",))]  # type: ignore[valid-type]

#: One value, because one action exists. The enum is not a placeholder for the
#: actions that are coming: a schema that offers `create` before `create` is
#: written is a schema that lies to the agent.
WorkoutAction = Literal["preview"]


class WorkoutStep(BaseModel):
    """One step of a prescribed session.

    Give exactly one of `duration_secs` and `distance_m` -- a step ends one way
    or the other. For `kind="repeat"`, give `iterations` and `repeat` instead,
    and nothing else.
    """

    kind: StepKind = Field(
        description=(
            "warmup, interval, recovery, cooldown, or repeat. Only an interval "
            "may be measured by distance; the rest are measured in time."
        )
    )
    duration_secs: float | None = Field(
        default=None, description="How long this step lasts, in seconds."
    )
    distance_m: float | None = Field(
        default=None, description="How far this step runs, in metres. Interval only."
    )
    iterations: int | None = Field(
        default=None, description="How many times a repeat group repeats. Minimum 2."
    )
    repeat: list["WorkoutStep"] = Field(
        default_factory=list,
        description=(
            "The steps inside a repeat group. A repeat group inside another one "
            "is refused: Garmin's acceptance of it has never been measured."
        ),
    )


WorkoutStep.model_rebuild()


class WorkoutPreview(BaseModel):
    """A composed session that has not been sent anywhere.

    `structure` is the exact payload an upload would carry. It is returned in
    full rather than summarised because the human approving it should be able to
    see what would land, and because the create action -- when it exists -- will
    take this structure back rather than re-composing it from the same
    arguments (§5). Re-composition is how a human approves one thing and a
    second one lands.
    """

    workout_name: str = Field(description="The name this session would be saved under.")
    sport: str = Field(description="Always 'running' in v0.")
    step_summary: list[str] = Field(
        description="One readable line per step, in the order they would run."
    )
    estimated_duration_secs: int = Field(
        description="Estimated length. Read `estimated_duration_basis` before using it."
    )
    estimated_duration_basis: str = Field(
        description="How the estimate was derived, and what it leaves out."
    )
    requests_made: int = Field(
        description="Garmin requests this call made. Always 0 for a preview."
    )
    structure: dict = Field(
        description="The exact structure an upload would send to Garmin."
    )
    approval: str = Field(
        description=(
            "Pass this to `garmin_workout_create` with the structure, unchanged. "
            "It fingerprints what was shown, so an edited or stale structure is "
            "refused rather than written."
        )
    )
    note: str = Field(description="What this call did not do.")


_PREVIEW_NOTE = (
    "Nothing was sent to Garmin: a preview composes and validates only, and makes "
    "zero requests. To write this session, show it to the human first, then pass "
    "`structure` and `approval` to `garmin_workout_create` with a date. Pass the "
    "structure through unchanged -- it is what was approved, and re-composing it "
    "from these arguments is what the approval marker exists to catch."
)


def _as_compose_steps(steps: list[WorkoutStep]) -> tuple[source_compose.Step, ...]:
    """The wire model, converted to the composer's own.

    Two models for one shape looks like duplication and is not: the wire model
    is a published schema the agent reads, and the composer's is a frozen
    dataclass with no pydantic in it. Collapsing them would put `server.py`'s
    schema concerns inside `source/`, which is the direction §11 exists to stop.
    """
    return tuple(
        source_compose.Step(
            kind=step.kind,
            duration_secs=step.duration_secs,
            distance_m=step.distance_m,
            iterations=step.iterations,
            repeat=_as_compose_steps(step.repeat),
        )
        for step in steps
    )


def _summarise(steps: list[WorkoutStep]) -> list[str]:
    """One line per step, in run order.

    Exists because `structure` is 40 lines of Garmin's shape per step, and a
    human approving a session should not have to read `conditionTypeId` to find
    out it is a 10 minute warmup.
    """
    lines: list[str] = []
    for step in steps:
        if step.kind == "repeat":
            lines.append(f"{step.iterations}x:")
            lines.extend(f"    {line}" for line in _summarise(step.repeat))
        elif step.duration_secs is not None:
            lines.append(f"{step.kind}: {step.duration_secs:g}s")
        elif step.distance_m is not None:
            lines.append(f"{step.kind}: {step.distance_m:g}m")
        else:
            # Unreachable through the composer, which refuses this -- but a
            # summary that silently dropped a step would be worse than one that
            # says it could not read it.
            lines.append(f"{step.kind}: no end condition")
    return lines


@mcp.tool(
    title="Garmin workouts",
    annotations=ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True
    ),
)
def garmin_workouts(
    action: Annotated[
        WorkoutAction,
        Field(description="Only 'preview' exists. It sends nothing to Garmin."),
    ],
    name: Annotated[str, Field(description="What to call this session.")],
    steps: Annotated[
        list[WorkoutStep],
        Field(description="The steps of the session, in the order they should run."),
    ],
    description: Annotated[
        str | None, Field(description="Optional note saved with the workout.")
    ] = None,
) -> WorkoutPreview:
    """Compose a running session and show what would be sent to Garmin.

    **Sends nothing.** A preview makes zero Garmin requests, so it cannot fail
    on a rate limit and costs nothing to run twice. There is no create or
    schedule action yet; this is the structure that one would carry.

    **This tool does not design the session.** It composes the steps it is
    given. Which distance, how many reps, how long the recovery, whether today
    should be hard at all -- those need how the legs feel, how much time there
    is and what hurts, which no Garmin endpoint holds. They belong in the
    conversation (docs/SCOPE.md §1).

    **v0 is time and distance with no pace or heart-rate target.** Every target
    Garmin offers is zone-based and this account's zones have never been
    fetched, so a target is refused rather than guessed. Ask for a target and
    the refusal says this.

    A malformed session is refused with the reason: a repeat group with no
    steps, fewer than two iterations, a step with no end condition, or one
    measured both ways.
    """
    if action != "preview":
        raise ToolError(
            f"unknown action {action!r}. Valid actions: preview. "
            "Creating and scheduling are not built yet."
        )
    if not steps:
        raise ToolError("a workout with no steps is not a session.")

    # Converted once. Converting twice would let the structure and the estimate
    # be built from two different readings of the same argument, which is a
    # smaller version of exactly the re-composition §5 forbids between preview
    # and create.
    composed_steps = _as_compose_steps(steps)

    try:
        structure = source_compose.compose(name, composed_steps, description)
        estimated, basis = source_compose.estimate_duration(composed_steps)
    except GarminSourceError as exc:
        raise ToolError(str(exc)) from exc

    return WorkoutPreview(
        workout_name=structure["workoutName"],
        sport="running",
        step_summary=_summarise(steps),
        estimated_duration_secs=estimated,
        estimated_duration_basis=basis,
        requests_made=0,
        structure=structure,
        approval=source_compose.approval_for(structure),
        note=_PREVIEW_NOTE,
    )


# --------------------------------------------------------------------------
# The write (docs/SCOPE.md §5, v1.5)
# --------------------------------------------------------------------------


class WriteResult(BaseModel):
    """What reached Garmin, and what to do if it only half did.

    Every field here exists so that a failure is actionable. A write that says
    only "something went wrong" leaves a template on an account with no way to
    find it, and the calendar -- which is what the next session reads -- would
    never show it.
    """

    action: str = Field(
        description=(
            "'uploaded' for a new workout, 'updated' when one of this name "
            "already existed and was replaced in place, keeping its id."
        )
    )
    workout_id: int | None = Field(
        description=(
            "Garmin's id for the template. Null only when the write succeeded "
            "and the id was not in a key this server recognises -- see warnings."
        )
    )
    scheduled: str = Field(
        description=(
            "'new' = put on the calendar by this call. 'already' = it was "
            "already on that date and no second entry was made. 'failed' = the "
            "workout exists and is on NO calendar; read the warnings."
        )
    )
    scheduled_workout_id: int | None = Field(
        description=(
            "The id undo needs. `unschedule_workout` takes this, NOT "
            "workout_id, so without it the calendar entry can only be removed "
            "in Garmin Connect."
        )
    )
    date: str = Field(description="The date this session was scheduled for.")
    requests_made: int = Field(description="Garmin requests this call made.")
    warnings: list[str] = Field(
        description="Empty when everything worked. Read every entry out loud to the human."
    )
    raw: dict = Field(
        description="Garmin's unparsed response, included only when an id could not be read."
    )


@mcp.tool(
    title="Create and schedule a Garmin workout",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
)
def garmin_workout_create(
    structure: Annotated[
        dict,
        Field(
            description=(
                "The `structure` a preview returned, passed through UNCHANGED. "
                "Do not rebuild it from the step list."
            )
        ),
    ],
    approval: Annotated[
        str,
        Field(description="The `approval` from the same preview, unchanged."),
    ],
    date: Annotated[
        str, Field(description="YYYY-MM-DD. The day this session goes on the calendar.")
    ],
) -> WriteResult:
    """Write a previewed session to Garmin and put it on the calendar.

    **This reaches the athlete's real Garmin account and their watch.** Show the
    preview to the human and get their agreement before calling it. The
    `approval` marker exists because the structure they agreed to must be the
    structure that lands -- so pass `structure` through exactly as the preview
    returned it. Rebuilding it from the same step list is the mistake this
    refuses: the human approves one thing and a second composition is written.

    **A name already on the account is replaced in place**, keeping its id, so
    every existing calendar entry pointing at it stays valid. That is how a
    re-prescription of "week 3 Thursday threshold" updates rather than leaving
    two templates nobody can tell apart. Two existing workouts of that name is
    refused rather than guessed at.

    **Creating and scheduling are one action.** If the schedule fails after the
    workout was written, `scheduled` is 'failed' and the warnings say so with
    the id -- the template exists and is on no calendar, which means nothing
    that reads the calendar will ever show it, including this server.

    This tool decides nothing about training. It does not pick the date, retry
    on another day, or adjust a session it thinks is wrong (docs/SCOPE.md §1).
    """
    if not source_write.writes_enabled():
        raise ToolError(str(source_write.WritesDisabled()))

    try:
        source_compose.validate_structure(structure)
    except GarminSourceError as exc:
        raise ToolError(f"{exc} Nothing was sent to Garmin.") from exc

    recomputed = source_compose.approval_for(structure)
    if approval.strip() != recomputed:
        raise ToolError(
            "This structure does not match its approval marker, so nothing was "
            "sent to Garmin. Either the structure changed after it was previewed, "
            "or it was rebuilt from the arguments instead of passed through. "
            "Preview it again, show the human the result, and send that."
        )

    try:
        outcome = source_write.create_and_schedule(
            source._connect(), source.tokenstore_path(), structure, date
        )
    except GarminSourceError as exc:
        raise ToolError(str(exc)) from exc

    return WriteResult(
        action=outcome.action,
        workout_id=outcome.workout_id,
        scheduled=outcome.scheduled,
        scheduled_workout_id=outcome.scheduled_workout_id,
        date=outcome.date,
        requests_made=outcome.requests_made,
        warnings=outcome.warnings,
        raw=outcome.raw,
    )
