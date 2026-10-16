"""Turn fetched payloads into answers (§5's response contracts).

Split from `fetch.py` on purpose: that module makes one request per distinct
source and nothing else, while every decoding rule here is a pure function over
a literal dict -- no client, no tokens, no budget. `metrics_for` at the bottom
is the one place the two meet, and it is the only entry point the tool layer
needs.

Each reading names its own source and the date it was measured on, because §5
requires it and because two sources can disagree -- resting HR is a `float` from
`get_rhr_day` and an `int` in the sleep payload -- and a number whose origin is
unstated is a number nobody can audit.

**A map row that resolves to the wrong type is `ERROR`, not `no_data`.** Half
the rows in `metrics.py` carry `verified=False` because their field name was
inferred rather than recorded. If an inferred path lands on a string where a
number was expected, that is *our* map being wrong, and `error` is this
vocabulary's word for our failure. Reporting it as `no_data` would be
indistinguishable from a day the watch was not worn, and the two need opposite
responses: one needs a one-line map fix, the other needs nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import client as source
from . import verdicts
from .extract import DECODERS, MISSING, at_path
from .fetch import Fetched, fetch
from .metrics import KIND_NUMBER, Metric, SourceCall, resolve, sources_for


@dataclass(frozen=True)
class Reading:
    """One metric's answer. Plain dataclass, not a pydantic model: `source/`
    does not know that MCP exists (the `AuthStatus` precedent in client.py)."""

    metric: str
    value: Any
    unit: str
    verdict: str
    source: str
    measured_on: str
    note: str = ""


def _decode(metric: Metric, payload: Any, date: str) -> Any:
    """Container, then decoder, then path -- in that order."""
    node = at_path(payload, metric.container) if metric.container else payload
    if node is MISSING:
        return MISSING

    if metric.decoder:
        decoder = DECODERS[metric.decoder]
        # `calendar_date` needs to know which date was asked for; the others
        # select from the payload alone.
        node = decoder(node, date) if metric.decoder == "calendar_date" else decoder(node)
        if node is MISSING:
            return MISSING

    return at_path(node, metric.path)


def read(
    metrics: tuple[Metric, ...],
    fetched: dict[SourceCall, Fetched],
    date: str,
) -> tuple[Reading, ...]:
    """Assemble one reading per requested metric, in the order asked."""
    out: list[Reading] = []

    for metric in metrics:
        if metric.source_method is None:
            # Answered from the map itself. §5: a metric with no source is
            # reported unsupported and never removed, so "this watch does not
            # measure SpO2" stays an answer rather than a silence.
            out.append(
                Reading(
                    metric=metric.key,
                    value=None,
                    unit=metric.unit,
                    verdict=verdicts.UNSUPPORTED,
                    source="",
                    measured_on=date,
                    note=metric.note,
                )
            )
            continue

        call = SourceCall(metric.source_method, metric.call)
        result = fetched.get(call)
        if result is None:
            # Defensive: `sources_for` and `fetch` are driven by the same rows,
            # so this is a wiring bug rather than a Garmin outcome -- and it is
            # our failure, which is what `error` means here.
            out.append(
                Reading(
                    metric=metric.key,
                    value=None,
                    unit=metric.unit,
                    verdict=verdicts.ERROR,
                    source=metric.source_method,
                    measured_on=date,
                    note="source was never fetched",
                )
            )
            continue

        if result.verdict != verdicts.PRESENT:
            out.append(
                Reading(
                    metric=metric.key,
                    value=None,
                    unit=metric.unit,
                    verdict=result.verdict,
                    source=metric.source_method,
                    measured_on=date,
                    note=result.detail or metric.note,
                )
            )
            continue

        value = _decode(metric, result.payload, date)

        if value is MISSING:
            out.append(
                Reading(
                    metric=metric.key,
                    value=None,
                    unit=metric.unit,
                    # An accumulating metric that is empty has not been earned
                    # yet rather than being absent -- the distinction v1.4 added
                    # and the reason the flag lives in the map.
                    verdict=verdicts.NOT_MATURED
                    if metric.accumulates
                    else verdicts.NO_DATA,
                    source=metric.source_method,
                    measured_on=date,
                    note=metric.note,
                )
            )
            continue

        if metric.kind == KIND_NUMBER and not isinstance(value, int | float):
            out.append(
                Reading(
                    metric=metric.key,
                    value=None,
                    unit=metric.unit,
                    verdict=verdicts.ERROR,
                    source=metric.source_method,
                    measured_on=date,
                    note=(
                        f"map path {metric.path!r} resolved to "
                        f"{type(value).__name__}, not a number -- this row's path "
                        "is wrong, not the account's data"
                    ),
                )
            )
            continue

        out.append(
            Reading(
                metric=metric.key,
                value=value,
                unit=metric.unit,
                verdict=verdicts.PRESENT,
                source=metric.source_method,
                measured_on=date,
            )
        )

    return tuple(out)


def metrics_for(
    keys: list[str] | tuple[str, ...],
    date: str | None = None,
) -> tuple[str, tuple[Reading, ...]]:
    """The whole pipeline, and the only function the tool layer calls.

    Returns the date actually used alongside the readings, because the tool must
    state what its dates mean (§5) and a caller that passed `None` has not been
    told yet.

    Order matters and is the point of the seam: resolve every requested metric
    to its distinct sources FIRST, then make one request each. Asking for
    training status, load balance and VO2 max is one request, not three,
    because `sources_for` collapsed them before anything was sent.
    """
    if date is None:
        # UTC rather than device-local, stated rather than assumed. Garmin's
        # dates are device-local and Mahi travels (§5), so this is the one
        # choice available to a server that cannot know the watch's timezone --
        # and the response says which it used.
        date = datetime.now(UTC).date().isoformat()

    wanted = resolve(keys)
    api = source._connect()
    fetched = fetch(api, sources_for(keys), source.tokenstore_path(), date)
    return date, read(wanted, fetched, date)
