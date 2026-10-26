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
from .source import metrics as metric_map
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


@mcp.tool(
    title="Garmin authentication status",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
def garmin_auth_status() -> AuthStatus:
    """Check whether this server can still talk to Garmin Connect.

    Makes a live authenticated call, so a success proves the stored tokens work
    right now. Use this first when other Garmin tools start failing.
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
    "Ask for everything you need in ONE call: the three HRV metrics come from "
    "one payload, so all three together cost a single Garmin request. `spo2` is "
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

    `hrv_last_night`, `hrv_weekly` and `hrv_status` all come out of one payload,
    so asking for all three costs one request. Ask for `hrv_weekly` alongside
    `hrv_last_night` rather than instead of it: a single night against no
    baseline is close to unreadable.

    **An empty answer is not an incapable watch.** A night the watch was not
    worn returns `no_data`, and `unsupported` means this account or device
    cannot answer at all -- `spo2` is the standing example, measured as
    unsupported on this watch rather than quietly dropped.
    """
    measured_on, readings = _read_group("health", list(metrics), date)
    return HealthReadings(
        date=measured_on, date_basis=DATE_BASIS, readings=readings
    )
