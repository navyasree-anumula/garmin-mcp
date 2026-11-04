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
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .source import client as source
from .source import compose as source_compose
from .source import metrics as metric_map
from .source import write as source_write
from .source import readings as source_readings
from .source import zones as source_zones
from .source import activities as source_activities
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
# What was actually done (docs/SCOPE.md §5, v1.8-A)
# --------------------------------------------------------------------------


class ExerciseSetDone(BaseModel):
    category: str = Field(description="Garmin's exercise category for this set.")
    sets: int | None
    reps: int | None
    max_weight_g: int | None = Field(
        description="Heaviest weight in GRAMS -- Garmin's own unit, not converted."
    )
    duration_s: float | None


class ActivityRow(BaseModel):
    """One activity, reduced to what a training decision needs.

    **No location and no account identity.** The projection behind this model is
    an allowlist, so nothing Garmin adds later can appear here either.
    """

    activity_id: int = Field(
        description="Ask about this activity's laps with this id. Returned again "
        "by any list covering the same date, so it cannot be lost."
    )
    name: str
    start_local: str = Field(description="Garmin's device-local start time, as given.")
    sport: str = Field(description="Garmin's activity type key, e.g. 'running'.")
    distance_m: float | None = Field(
        description="Metres -- INFERRED from the account's metric setting, not read."
    )
    duration_s: float | None = Field(description="Seconds -- inferred, as above.")
    moving_duration_s: float | None
    avg_pace_min_km: str | None = Field(
        description=(
            "Average pace as m:ss per km, derived from Garmin's own average "
            "speed. None where a pace is meaningless -- a strength circuit has "
            "no distance, and no pace is the truth rather than 0:00."
        )
    )
    avg_hr_bpm: int | None
    max_hr_bpm: int | None
    aerobic_training_effect: float | None
    anaerobic_training_effect: float | None
    training_effect_label: str | None = Field(
        description="Garmin's own word for the effect, where it gives one."
    )
    training_load: float | None
    hr_time_in_zone_s: dict[int, float] = Field(
        description=(
            "Seconds in each heart-rate zone Garmin reported, keyed by zone "
            "number. **Never padded**: a missing zone means Garmin did not say, "
            "while a zero would be the claim 'no time in that zone'. This is "
            "what answers whether an easy run was actually easy."
        )
    )
    workout_id: int | None = Field(
        description=(
            "The scheduled workout this activity came from, when it came from "
            "one -- the link between planned and done. None for an unplanned "
            "session; a zero from Garmin is reported as None, because a zero "
            "read as an id would point at a workout that does not exist."
        )
    )
    lap_count: int | None
    has_splits: bool | None
    exercise_sets: list[ExerciseSetDone] = Field(
        description="What a strength session actually did, as Garmin summarises it."
    )


class ActivityListResult(BaseModel):
    activities: list[ActivityRow]
    date_basis: str
    requests_made: int
    requests_basis: str = Field(
        description="Whether the request count was measured or derived."
    )
    note: str


@mcp.tool(
    title="Garmin past activities",
    annotations=ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True
    ),
)
def garmin_activities(
    start_date: Annotated[str, Field(description="YYYY-MM-DD, inclusive.")],
    end_date: Annotated[str, Field(description="YYYY-MM-DD, inclusive.")],
    sport: Annotated[
        str | None,
        Field(
            description=(
                "Optional Garmin activity type to filter by -- 'running', "
                "'cycling', 'strength_training' and so on. Omit for everything."
            )
        ),
    ] = None,
) -> ActivityListResult:
    """What was actually done in a date window, to compare against what was planned.

    **This is the other half of the loop.** Every other tool here reads a
    measurement or writes a prescription; this one says what happened.
    `workout_id` is the link: it is set when an activity came from a scheduled
    workout, so a week's plan and a week's training can be put side by side.

    **No location and no account identity ever leaves this tool.** Garmin's
    activity row carries start and end coordinates, a location name, and the
    account's own display name, full name and profile image URLs in *every* row.
    This tool names the fields it returns and drops everything else, so a field
    Garmin adds tomorrow is dropped too (docs/SCOPE.md §9).

    **The window is capped at 31 days** and a wider one is refused rather than
    truncated. The reason is not politeness: the underlying call pages through
    history *inside* a single request, so a wide window spends far more of
    Garmin's patience than the local request budget can see.

    **It computes no totals and offers no verdict on the week.** Rows out. What
    a week of this means needs how the legs feel, how much time there was and
    what hurts, which no Garmin endpoint holds (docs/SCOPE.md §1).

    **`hr_time_in_zone_s` is the one to ask for when the question is whether an
    easy run stayed easy** -- it needs no zone table and no detail call.

    Per-lap splits are not here yet: the list carries only an aggregate per
    split type, with no heart rate, so the per-lap call has to be probed before
    it is designed.
    """
    try:
        result = source_activities.read_window(start_date, end_date, sport)
    except GarminSourceError as exc:
        raise ToolError(str(exc)) from exc

    return ActivityListResult(
        activities=[
            ActivityRow(
                activity_id=a.activity_id,
                name=a.name,
                start_local=a.start_local,
                sport=a.sport,
                distance_m=a.distance_m,
                duration_s=a.duration_s,
                moving_duration_s=a.moving_duration_s,
                avg_pace_min_km=a.avg_pace_min_km,
                avg_hr_bpm=a.avg_hr_bpm,
                max_hr_bpm=a.max_hr_bpm,
                aerobic_training_effect=a.aerobic_training_effect,
                anaerobic_training_effect=a.anaerobic_training_effect,
                training_effect_label=a.training_effect_label,
                training_load=a.training_load,
                hr_time_in_zone_s=a.hr_time_in_zone_s,
                workout_id=a.workout_id,
                lap_count=a.lap_count,
                has_splits=a.has_splits,
                exercise_sets=[
                    ExerciseSetDone(
                        category=s.category,
                        sets=s.sets,
                        reps=s.reps,
                        max_weight_g=s.max_weight_g,
                        duration_s=s.duration_s,
                    )
                    for s in a.exercise_sets
                ],
            )
            for a in result.activities
        ],
        date_basis=DATE_BASIS,
        requests_made=result.requests_made,
        requests_basis=result.requests_basis,
        note=result.note,
    )


# --------------------------------------------------------------------------
# Heart-rate zones -- configuration, not a measurement (docs/SCOPE.md §5, v1.8)
# --------------------------------------------------------------------------


class HeartRateZone(BaseModel):
    zone: int = Field(description="Zone number, 1 to 5.")
    floor_bpm: int = Field(description="The lowest heart rate in this zone, measured.")
    ceiling_bpm: int | None = Field(
        description=(
            "The highest heart rate in this zone, DERIVED -- Garmin stores "
            "floors only. None means it could not be derived, and "
            "`ceiling_basis` says why."
        )
    )
    ceiling_basis: str = Field(
        description="Where the ceiling came from, including when it is an inference."
    )


class ZoneProfile(BaseModel):
    sport: str = Field(description="Garmin's sport key for this profile.")
    training_method: str = Field(
        description=(
            "How Garmin computed these zones -- percentage of max, of "
            "threshold, or of reserve. The same zone number means different "
            "bpm under different methods."
        )
    )
    max_hr_bpm: int | None = Field(description="Max heart rate Garmin used, bpm.")
    threshold_hr_bpm: int | None = Field(
        description="Lactate threshold heart rate Garmin used, bpm."
    )
    resting_hr_used_bpm: int | None = Field(
        description="Resting heart rate Garmin used for these zones, bpm."
    )
    zones: list[HeartRateZone]
    change_state: str = Field(description="Garmin's own change-state string.")


class ZoneReadings(BaseModel):
    profiles: list[ZoneProfile]
    requests_made: int = Field(description="Garmin requests this call made.")
    note: str = Field(description="What these numbers are and are not.")


@mcp.tool(
    title="Garmin heart rate zones",
    annotations=ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True
    ),
)
def garmin_zones() -> ZoneReadings:
    """Read the heart-rate zones this account has configured.

    **Configuration, not a measurement.** These numbers have no date: they are
    how the watch is set up, not something it recorded on a day. That is why
    they are not metrics in `garmin_training`.

    **Garmin stores zone FLOORS, so every ceiling here is derived** and each
    zone says which way: the next zone's floor (measured), or -- for zone 5
    only -- `maxHeartRateUsed`, which is an **inference**, because no field
    carries the top of the top zone. A zone whose ceiling could not be derived
    reports `None` and says so rather than guessing, because a fabricated bound
    becomes a wrong target on a watch.

    **It reports every profile Garmin returns and assumes nothing about their
    order.** On 2026-11-04 this account returned exactly one, and which sport it
    belongs to is in `sport` -- read it rather than taking the first row as the
    one you wanted.

    `max_hr_bpm`, `threshold_hr_bpm` and `resting_hr_used_bpm` are health
    numbers sitting in a payload that reads as settings; they travel like any
    other health data (docs/SCOPE.md §9).

    **Which zone to run in is not here.** That needs how the legs feel, how much
    time there is and what the session is for, none of which any Garmin endpoint
    holds, so it belongs in the conversation (docs/SCOPE.md §1).
    """
    try:
        reading = source_zones.read_zones()
    except GarminSourceError as exc:
        raise ToolError(str(exc)) from exc

    return ZoneReadings(
        profiles=[
            ZoneProfile(
                sport=p.sport,
                training_method=p.training_method,
                max_hr_bpm=p.max_hr_bpm,
                threshold_hr_bpm=p.threshold_hr_bpm,
                resting_hr_used_bpm=p.resting_hr_used_bpm,
                zones=[
                    HeartRateZone(
                        zone=z.zone,
                        floor_bpm=z.floor_bpm,
                        ceiling_bpm=z.ceiling_bpm,
                        ceiling_basis=z.ceiling_basis,
                    )
                    for z in p.zones
                ],
                change_state=p.change_state,
            )
            for p in reading.profiles
        ],
        requests_made=reading.requests_made,
        note=reading.note,
    )


# --------------------------------------------------------------------------
# The exercise catalogue -- a lookup, not an endpoint (docs/SCOPE.md §5)
# --------------------------------------------------------------------------


class ExerciseMatch(BaseModel):
    """One catalogue entry. `name` is the field to pass back to a step."""

    name: str = Field(
        description=(
            "Garmin's display name, and the value to give a step's `exercise`. "
            "Every name returned here resolves unambiguously, so it can be "
            "copied straight into a workout."
        )
    )
    category: str = Field(description="Garmin's category, e.g. SQUAT.")
    exercise: str = Field(description="Garmin's exercise key, e.g. GOBLET_SQUAT.")


class ExerciseSearch(BaseModel):
    """What matched, how many there were, and whether the list was cut short."""

    search: str = Field(description="The term that was searched for.")
    matches: list[ExerciseMatch] = Field(
        description="The matches, best first: exact name, then names starting with the term, then the rest."
    )
    shown: int = Field(description="How many matches are in this response.")
    total_matches: int = Field(
        description=(
            "How many matched in total. Compare with `shown`: a larger total "
            "means the list was capped and there are more."
        )
    )
    truncated: bool = Field(
        description="True when `total_matches` exceeds `shown`. Raise `limit` or narrow the search."
    )
    requests_made: int = Field(
        description="Garmin requests this call made. Always 0 -- the catalogue ships in the library."
    )
    note: str = Field(description="What this call did and did not do.")


_EXERCISE_NOTE = (
    "Nothing was sent to Garmin: the exercise catalogue is a constant that ships "
    "inside the library, so this call reaches no endpoint, spends no request "
    "budget and cannot hit a rate limit. Use it when `garmin_workout_create` or "
    "a preview refuses an exercise name as ambiguous -- pass one of the `name` "
    "values back verbatim and it will resolve."
)


@mcp.tool(
    title="Search Garmin's exercise catalogue",
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def garmin_exercises(
    search: Annotated[
        str,
        Field(
            description=(
                "Words to look for in the exercise name. All of them must "
                "appear, in any order, so 'squat goblet' finds the same entry "
                "as 'goblet squat'. Case does not matter."
            )
        ),
    ],
    limit: Annotated[
        int, Field(description="How many matches to return. Default 25.")
    ] = source_compose.DEFAULT_SEARCH_LIMIT,
) -> ExerciseSearch:
    """Look up what Garmin calls an exercise, without touching Garmin.

    **Zero requests.** The catalogue is 1527 entries compiled into the library,
    not an endpoint, so this is free, cannot fail on a rate limit and costs
    nothing to run repeatedly.

    **This exists because refusing was not enough.** A strength step must name
    one catalogue entry, and an ambiguous name is refused rather than resolved
    to the nearest match -- the right call, since a guess puts a different
    exercise on the watch under a name that reads correct. But the first live
    strength session then had no way to resolve the ambiguity except to guess
    again, and spent three turns doing it. This is the looking.

    Every `name` returned resolves unambiguously, so the answer to a refusal is
    to search, show the human the options, and pass a `name` back verbatim.
    """
    try:
        matches, total = source_compose.search_exercises(search, limit)
    except GarminSourceError as exc:
        raise ToolError(str(exc)) from exc

    if not matches:
        raise ToolError(
            f"no Garmin exercise name contains all of {search!r}. The catalogue "
            "holds 1527 entries; try one broader movement word, such as 'squat', "
            "'lunge', 'press', 'row' or 'plank'."
        )

    return ExerciseSearch(
        search=search,
        matches=[ExerciseMatch(**entry) for entry in matches],
        shown=len(matches),
        total_matches=total,
        truncated=total > len(matches),
        requests_made=0,
        note=_EXERCISE_NOTE,
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

#: Derived from `compose` for the same reason the step kinds are: a sport the
#: schema offers and the composer cannot build is a schema that lies.
WorkoutSport = Literal[tuple(source_compose.SPORTS)]  # type: ignore[valid-type]

#: The literal example carried in the `steps` description, as a constant rather
#: than as prose inside it, so a test can parse this exact string and compose
#: it. A documented example that no longer works is worse than no example: it
#: reads as verified. Two live sessions were lost guessing this shape, because
#: it lives behind a recursive `$ref` in the schema and not every client renders
#: one -- so it is spelled out where the agent cannot miss it, and asserted
#: where we cannot break it.
REPEAT_GROUP_EXAMPLE = (
    '[{"kind": "repeat", "iterations": 4, "repeat": '
    '[{"kind": "exercise", "duration_secs": 40, "exercise": "Goblet Squat"}, '
    '{"kind": "rest", "duration_secs": 20}]}]'
)


class WorkoutStep(BaseModel):
    """One step of a prescribed session.

    Give exactly one of `duration_secs` and `distance_m` -- a step ends one way
    or the other. For `kind="repeat"`, give `iterations` and `repeat` instead,
    and nothing else. The nested steps go in a field called **`repeat`**.

    **Unknown fields are refused, not ignored.** pydantic's default is to drop
    them, and on 2026-10-31 that cost a live session: a client sent the nested
    steps under `steps` instead of `repeat`, the key vanished, and the server
    refused with "a repeat group with no steps repeats nothing" -- blaming the
    group for being empty rather than naming the field that was never read. The
    agent then guessed field names for several more turns. A silently dropped
    input is the §5 failure this project refuses everywhere else, and the schema
    boundary is where it is cheapest to catch.
    """

    model_config = ConfigDict(extra="forbid")

    kind: StepKind = Field(
        description=(
            "For sport='running': warmup, interval, recovery, cooldown, or "
            "repeat. Only an interval may be measured by distance; the rest are "
            "measured in time. For sport='strength': exercise, rest, warmup, "
            "cooldown, or repeat, all measured in time -- a circuit can warm up "
            "and cool down, and those two steps carry no exercise name. A kind "
            "used with the wrong sport is refused and the refusal lists that "
            "sport's kinds."
        )
    )
    exercise: str | None = Field(
        default=None,
        description=(
            "The exercise this step performs, for kind='exercise' only. Matched "
            "against Garmin's own catalogue, which is what it stores -- 'goblet "
            "squat', 'push-up', 'mountain climber'. An ambiguous term is refused "
            "with the candidates listed rather than resolved to the nearest "
            "match. When that happens, call `garmin_exercises` to search the "
            "catalogue -- it makes no Garmin requests -- and pass one of its "
            "`name` values back verbatim."
        ),
    )
    duration_secs: float | None = Field(
        default=None, description="How long this step lasts, in seconds."
    )
    distance_m: float | None = Field(
        default=None, description="How far this step runs, in metres. Interval only."
    )
    pace_min_km: list[str] | None = Field(
        default=None,
        description=(
            "Optional target pace range for a RUNNING step, in min/km, as two "
            "strings with the faster bound first -- ['4:20', '4:40']. Refused "
            "rather than adjusted if the bounds are equal or the wrong way "
            "round, if the seconds are 60 or more, if the seconds have a "
            "fraction, or if either bound is outside 2:30-12:00 per km. A pace "
            "on a strength step, or on a repeat group instead of the steps "
            "inside it, is refused. Heart-rate, power, cadence and zone-based "
            "targets are not available: they depend on a profile this account "
            "has never been read for."
        ),
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
    sport: str = Field(description="The sport this session was composed for.")
    step_summary: list[str] = Field(
        description="One readable line per step, in the order they would run."
    )
    estimated_duration_secs: int = Field(
        description=(
            "Estimated length. Read `estimated_duration_basis` before using it. "
            "Garmin does NOT recompute this on upload (measured 2026-10-31), so "
            "whatever is here is the number the calendar will display, with no "
            "basis sentence beside it. A distance step is estimated only when it "
            "was given a pace; without one it contributes 0."
        )
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


_TRAILING_REST_NOTE = (
    " NOTE ON THIS CIRCUIT: its last round ends with a rest step. A repeat group "
    "cannot vary by iteration, so the rest after the final exercise cannot be "
    "dropped while keeping the rest between rounds -- the watch will hold that "
    "last rest. Read this out: it is a property of the session being approved, "
    "not of this server."
)


def _ends_on_a_rest(structure: dict) -> bool:
    """Whether any repeat group's last child is a rest step.

    Read off the composed structure rather than the step list, because it is a
    statement about what Garmin will run, and the structure is what Garmin gets.
    """
    for item in structure["workoutSegments"][0]["workoutSteps"]:
        if item.get("type") != "RepeatGroupDTO":
            continue
        children = item.get("workoutSteps") or []
        if children and (children[-1].get("stepType") or {}).get("stepTypeKey") == "rest":
            return True
    return False


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
            exercise=step.exercise,
            pace_min_km=(
                tuple(step.pace_min_km) if step.pace_min_km is not None else None
            ),
        )
        for step in steps
    )


def _pace_suffix(step: WorkoutStep) -> str:
    """` @ 4:20-4:40/km`, or nothing -- rendered by the one converter.

    **Not re-parsed from the argument.** The words a human approves and the
    bytes Garmin stores have to be two renderings of one value; computing them
    separately is how somebody approves the right pace on the wrong workout
    (SCOPE §15, Pass A.1 and B.3). So this calls `pace_target` -- the same
    function the structure was built from -- and shows what it produced.
    """
    if step.pace_min_km is None:
        return ""
    return f" @ {source_compose.pace_target(step.pace_min_km).rendered}"


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
        elif step.kind == "exercise" and step.duration_secs is not None:
            # Resolved, not echoed. The whole point of the line is to show which
            # catalogue entry Garmin will store, and the term the agent typed is
            # exactly what cannot be trusted to be that entry.
            entry = source_compose.resolve_exercise(step.exercise)
            lines.append(
                f"exercise: {entry['name']} {step.duration_secs:g}s "
                f"[{entry['category']}/{entry['exercise']}]"
            )
        elif step.duration_secs is not None:
            lines.append(f"{step.kind}: {step.duration_secs:g}s{_pace_suffix(step)}")
        elif step.distance_m is not None:
            lines.append(f"{step.kind}: {step.distance_m:g}m{_pace_suffix(step)}")
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
        Field(
            description=(
                "The steps of the session, in the order they should run. "
                "A repeat group's nested steps go in a field called `repeat` -- "
                "NOT `steps`, `children` or `items`. A 4-round circuit is ONE "
                "repeat group, written exactly like this: "
                f"{REPEAT_GROUP_EXAMPLE} "
                "-- and the same circuit written flat is that exercise/rest "
                "pair listed four times here, which Garmin accepts equally. "
                "Unknown field names are refused, not ignored, so a guess is "
                "told which field it got wrong."
            )
        ),
    ],
    description: Annotated[
        str | None, Field(description="Optional note saved with the workout.")
    ] = None,
    sport: Annotated[
        WorkoutSport,
        Field(
            description=(
                "'running' (the default) or 'strength'. A strength session is a "
                "circuit of named exercises held for a time, with timed rests."
            )
        ),
    ] = "running",
) -> WorkoutPreview:
    """Compose a session and show what would be sent to Garmin.

    **Sends nothing.** A preview makes zero Garmin requests, so it cannot fail
    on a rate limit and costs nothing to run twice. There is no create or
    schedule action yet; this is the structure that one would carry.

    **This tool does not design the session.** It composes the steps it is
    given. Which distance, how many reps, how long the recovery, whether today
    should be hard at all -- those need how the legs feel, how much time there
    is and what hurts, which no Garmin endpoint holds. They belong in the
    conversation (docs/SCOPE.md §1).

    **One target, and only on a running step: an explicit pace range.** Give a
    step `pace_min_km` as two paces in min/km with the faster bound first --
    `["4:20", "4:40"]` -- and the preview shows the range in min/km while the
    structure carries it in metres per second, both from one conversion. **Every
    other target is still refused**: heart rate, power, cadence, resistance and
    pace *zones* all depend on a profile this account has never been read for,
    and a pace on a strength step is refused because a circuit is prescribed by
    time. Ask for one of those and the refusal says which it is.

    **Confirmed live on 2026-10-31**, workout 1746821178: Connect displays a
    prescribed 4:20-4:40/km range exactly as given, which settles the key
    string, the unit and which value carries the faster bound. What is still
    open is the watch's own behaviour -- whether it alerts when the athlete runs
    outside the range, and in which direction. The data it is working from is
    confirmed correct.

    **A strength session may warm up and cool down.** Garmin accepts a timed
    `warmup` and `cooldown` inside a `strength_training` workout -- confirmed
    live on 2026-10-31, workout 1746822994, accepted with no warnings. Those two
    steps carry no exercise name by design.

    **A strength session is a circuit.** Put the round in a repeat group --
    exercise, rest, exercise, rest -- and give the group its iterations. Each
    exercise names one entry from Garmin's catalogue and is held for
    `duration_secs`; reps and weight are not built. An ambiguous exercise name
    is refused with the candidates listed, so the human approves the entry
    Garmin will actually store rather than the word the agent typed.

    **The nested steps of a repeat group go in `repeat`.** The `steps`
    parameter's own description carries a literal example to copy. Two live
    sessions were lost guessing this, because the nested shape lives behind a
    recursive `$ref` in the schema and not every client renders one -- so it is
    written out in prose here, where it cannot be missed. Writing the rounds
    out flat, with no repeat group at all, is equally valid and is what the
    first successful strength session actually did.

    **Garmin accepts a named exercise measured in time** -- confirmed by the
    first live create on 2026-10-31, workout 1746708075, scheduled and visible
    on the watch. The library builds named exercises by *reps* and timed steps
    *without* names, so the combination was this server's and was labelled
    unverified until that create. It no longer is.

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
        structure = source_compose.compose(
            name, composed_steps, description, sport=sport
        )
        estimated, basis = source_compose.estimate_duration(composed_steps)
    except GarminSourceError as exc:
        raise ToolError(str(exc)) from exc

    note = _PREVIEW_NOTE
    if _ends_on_a_rest(structure):
        note += _TRAILING_REST_NOTE

    return WorkoutPreview(
        workout_name=structure["workoutName"],
        sport=sport,
        step_summary=_summarise(steps),
        estimated_duration_secs=estimated,
        estimated_duration_basis=basis,
        requests_made=0,
        structure=structure,
        approval=source_compose.approval_for(structure),
        note=note,
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
            "workout_id. Null is no longer a dead end: `garmin_workout_unschedule` "
            "finds this id from the calendar by name and date, which is why "
            "nothing stores it (docs/SCOPE.md §15.13)."
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
    id_keys: dict = Field(
        description=(
            "Which response key each id was read from. Recorded so a SUCCESSFUL "
            "write documents Garmin's response shape -- the raw response is "
            "attached only on a miss, so a success used to teach nothing."
        )
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
        id_keys=outcome.id_keys,
    )


# --------------------------------------------------------------------------
# Undo (docs/SCOPE.md §15.13, v1.7)
# --------------------------------------------------------------------------


class UnscheduleResult(BaseModel):
    """A calendar entry that is gone, said plainly enough to survive a summary.

    The one fact this model exists to carry is that the **workout survives**.
    An undo that reads as "the workout is gone" produces a duplicate on the next
    write, because the next write resolves by name and the name is still taken.
    """

    name: str = Field(
        description="The workout whose calendar entry was removed, as matched."
    )
    date: str = Field(description="The date it was removed from.")
    workout_id: int = Field(
        description="The template, which still exists. Nothing deleted it."
    )
    scheduled_workout_id: int = Field(
        description=(
            "The calendar entry that was removed. Looked up from Garmin's "
            "calendar by name and date, which is why this call needed no id."
        )
    )
    removed: bool = Field(
        description=(
            "True only after Garmin accepted the removal. There is no false "
            "case: everything else raises, because a 'false' here would read as "
            "an undo that happened."
        )
    )
    template_kept: bool = Field(
        description=(
            "Always true, and said rather than implied. The workout is still in "
            "the Garmin Connect library and can be scheduled again. Removing "
            "the template itself is a separate act this server does not do."
        )
    )
    same_workout_left_on_date: int = Field(
        description=(
            "Entries for THIS workout still on that date, normally 0. Garmin "
            "permits the same workout twice on one day and a removal takes one "
            "entry at a time, so a non-zero value here means calling again "
            "removes the next one. Also stated in warnings."
        )
    )
    entries_left_on_date: int = Field(
        description=(
            "Scheduled workouts of ANY kind still on that date. Separate from "
            "the field above because they answer different questions: that one "
            "says whether to call again, this one says whether the day is now "
            "empty."
        )
    )
    requests_made: int = Field(description="Garmin requests this call made.")
    warnings: list[str] = Field(
        description="Empty when everything worked. Read every entry out loud to the human."
    )
    id_keys: dict = Field(
        description=(
            "Which key inside the calendar item carried each id. The calendar "
            "item's shape had no measured key names before this tool existed, "
            "so a successful undo is also the measurement."
        )
    )
    note: str = Field(
        description="One sentence a summarising client can carry instead of the fields."
    )


@mcp.tool(
    title="Take a Garmin workout off the calendar",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=True,
    ),
)
def garmin_workout_unschedule(
    name: Annotated[
        str,
        Field(
            description=(
                "The workout's name, exactly as it was written. The match is "
                "case-sensitive, the same way a create resolves it."
            )
        ),
    ],
    date: Annotated[
        str,
        Field(description="YYYY-MM-DD. The date to take it off."),
    ],
) -> UnscheduleResult:
    """Remove a scheduled session from the athlete's real Garmin calendar.

    **Name and date. Never an id, including one from a create earlier in this
    conversation.** The id Garmin needs is looked up from the calendar here,
    which is the whole point: the calendar is the current state and a remembered
    id is a memory of one. It also means an undo still works in a conversation
    where the create's response was summarised and the ids were dropped.

    **The workout itself is NOT deleted.** This takes the entry off the
    calendar and leaves the template in the Garmin Connect library, which is
    what makes it the right undo for a session put on the wrong day: remove it
    here, then create it again for the right date and the same template is
    reused. Deleting a workout permanently is not something this server does.

    **Three different refusals, and they mean different things.** No workout of
    that name; nothing of that name on that date (which says how many sessions
    that date does hold); and a calendar month that could not be read at all --
    the last of which is *not* evidence the entry is absent. None of them
    changes anything on the account.

    This tool decides nothing about training. It does not pick which session was
    a mistake (docs/SCOPE.md §1).
    """
    if not source_write.writes_enabled():
        raise ToolError(str(source_write.WritesDisabled()))

    try:
        outcome = source_write.unschedule(
            source._connect(), source.tokenstore_path(), name, date
        )
    except GarminSourceError as exc:
        raise ToolError(str(exc)) from exc

    note = (
        f"{outcome.name!r} is off the calendar for {outcome.date}. The workout "
        f"itself (id {outcome.workout_id}) still exists in Garmin Connect and "
        "can be scheduled again; only the calendar entry was removed."
    )
    same = outcome.same_workout_left_on_date
    if same:
        note += (
            f" {same} further entr{'y' if same == 1 else 'ies'} for it remain "
            f"on {outcome.date} -- call this again to remove the next one."
        )
    left = outcome.entries_left_on_date
    if left:
        note += (
            f" {outcome.date} still holds {left} scheduled "
            f"workout{'' if left == 1 else 's'} in total."
        )
    else:
        note += f" Nothing else is scheduled on {outcome.date}."

    return UnscheduleResult(
        name=outcome.name,
        date=outcome.date,
        workout_id=outcome.workout_id,
        scheduled_workout_id=outcome.scheduled_workout_id,
        removed=outcome.removed,
        template_kept=True,
        same_workout_left_on_date=outcome.same_workout_left_on_date,
        entries_left_on_date=outcome.entries_left_on_date,
        requests_made=outcome.requests_made,
        warnings=outcome.warnings,
        id_keys=outcome.id_keys,
        note=note,
    )
