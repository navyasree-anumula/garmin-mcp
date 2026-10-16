"""The metric -> source map (§5, v1.4). The only thing that knows where a
metric lives.

**A metric names its source; a source never names the metric.** In four of nine
known cases the endpoint named after a metric is not the endpoint that answers
it -- VO2 max is inside `training_status` while `get_max_metrics` returns
`list(0)` -- so this is a tested artifact rather than an inference anyone makes
at a call site.

**Rows carry their own provenance.** `verified` says the dotted path was read
off a payload this project has actually received, and every unverified row says
in its note what would confirm it. The alternative is a map that looks
authoritative and is partly guesswork, which is the failure mode §5's own
epistemics exist to prevent: a verdict is a measurement with a date on it, and
so is a field path.

**Lactate threshold is deliberately absent.** `get_lactate_threshold` has never
been called by this project -- it is not among the probe's 33 candidates -- and
a map row for it would be invention. It arrives when 5c measures it, which is
the one thing that would make it a row rather than a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

#: How a source method is called. The probe's vocabulary, narrowed to what the
#: training sources need; `range` arrives with the trend metrics (§5's range
#: contract) and `activities` stays in the probe.
CALL_DATE = "date"
CALL_NONE = "none"

#: What kind of answer a row produces. A `phrase` is Garmin's own wording for
#: an opaque code and has no unit; a `number` must have one (§5).
KIND_NUMBER = "number"
KIND_PHRASE = "phrase"


@dataclass(frozen=True)
class Metric:
    """One answerable metric, and everything needed to answer it.

    `container` + `decoder` + `path` are applied in that order: walk to the
    container, hand it to the decoder, then read the path out of what the
    decoder returned. An empty `container` hands the decoder the whole payload;
    an empty `decoder` reads the path straight out of the container.
    """

    key: str
    group: str
    source_method: str | None
    call: str = CALL_DATE
    container: str = ""
    decoder: str = ""
    path: str = ""
    unit: str = ""
    kind: str = KIND_NUMBER
    accumulates: bool = False
    verified: bool = False
    note: str = ""


METRICS: tuple[Metric, ...] = (
    # ---- get_training_status: three metrics, one payload -------------------
    # This is the row set that makes per-call coalescing worth having: asking
    # for all three costs one request, not three.
    Metric(
        key="training_status",
        group="training",
        source_method="get_training_status",
        container="mostRecentTrainingStatus.latestTrainingStatusData",
        decoder="primary_device",
        path="trainingStatusFeedbackPhrase",
        kind=KIND_PHRASE,
        verified=False,
        note=(
            "The device-keyed container is recorded (5b: "
            "latestTrainingStatusData.<deviceId>.trainingStatus). The sibling "
            "`trainingStatus: int` is deliberately never read -- no mapping from "
            "that code to a meaning has been sourced, so the phrase is the "
            "answer. The phrase FIELD NAME is recorded; its position inside the "
            "device DTO is inferred and 5c confirms it."
        ),
    ),
    Metric(
        key="load_balance",
        group="training",
        source_method="get_training_status",
        container="mostRecentTrainingLoadBalance.metricsTrainingLoadBalanceDTOMap",
        decoder="primary_device",
        path="trainingBalanceFeedbackPhrase",
        kind=KIND_PHRASE,
        verified=False,
        note=(
            "Container recorded in 5b via its sibling "
            "metricsTrainingLoadBalanceDTOMap.<deviceId>.monthlyLoadAerobicLow. "
            "The phrase field name is recorded; its position is inferred. 5c "
            "confirms both this and training_status in one request."
        ),
    ),
    Metric(
        key="vo2_max",
        group="training",
        source_method="get_training_status",
        path="mostRecentVO2Max.generic.vo2MaxValue",
        unit="mL/kg/min",
        verified=True,
        note=(
            "Recorded 5b. NOT get_max_metrics, which answers a single date with "
            "list(0) -- the finding that made this map a tested artifact. "
            "`mostRecentVO2Max.cycling` is null on this account: running only."
        ),
    ),
    # ---- get_training_readiness: list(4) a day ----------------------------
    Metric(
        key="readiness",
        group="training",
        source_method="get_training_readiness",
        decoder="latest_timestamp",
        path="score",
        unit="index, scale unconfirmed",
        verified=False,
        note=(
            "The root being list(4) for one day, selected by `timestamp` rather "
            "than by index, is recorded (5b). The score's own field name and its "
            "scale are NOT: both are inferred and 5c settles them. The unit "
            "string says so rather than asserting 0-100, because §5 forbids a "
            "bare number and a confidently wrong scale is worse than an honest "
            "one."
        ),
    ),
    Metric(
        key="recovery_time",
        group="training",
        source_method="get_training_readiness",
        decoder="latest_timestamp",
        path="recoveryTime",
        unit="hours (inferred)",
        verified=False,
        note=(
            "Same payload as readiness, so it costs no extra request. Neither "
            "the field name nor the unit is recorded anywhere -- 5c confirms "
            "both. Flagged rather than omitted because it is the second half of "
            "the question readiness answers."
        ),
    ),
    # ---- get_race_predictions: no arguments -------------------------------
    # Four metrics, one request, and the call shape is the whole story: this
    # method accepts zero arguments or all three and raises ValueError for
    # anything between, which is why the probe recorded an ERROR for a month
    # that was entirely our own.
    Metric(
        key="race_5k",
        group="training",
        source_method="get_race_predictions",
        call=CALL_NONE,
        path="time5K",
        unit="s (inferred)",
        verified=True,
        note=(
            "Field name recorded 5b. Two things about this row are NOT recorded "
            "and 5c settles both: the unit (a bare int with no unit in its name "
            "-- v1.4's per-field decision, made here as seconds inferred from "
            "magnitude), and the ROOT SHAPE. The response is assumed to be a "
            "dict carrying these four fields; if it is a list, this path "
            "resolves to nothing and the row needs a decoder."
        ),
    ),
    Metric(
        key="race_10k",
        group="training",
        source_method="get_race_predictions",
        call=CALL_NONE,
        path="time10K",
        unit="s (inferred)",
        verified=True,
        note="See race_5k for the unit decision.",
    ),
    Metric(
        key="race_half",
        group="training",
        source_method="get_race_predictions",
        call=CALL_NONE,
        path="timeHalfMarathon",
        unit="s (inferred)",
        verified=True,
        note="See race_5k for the unit decision.",
    ),
    Metric(
        key="race_marathon",
        group="training",
        source_method="get_race_predictions",
        call=CALL_NONE,
        path="timeMarathon",
        unit="s (inferred)",
        verified=True,
        note="See race_5k for the unit decision.",
    ),
    # ---- A row with no source, which is the point --------------------------
    # §13.9's finding 8.a: a test asserting every metric resolves to a real
    # library method would have to call this row a failure. It is not a
    # failure. The metric is reported unsupported and never removed (§5), so
    # "this watch does not measure SpO2" stays an answer the agent can give.
    Metric(
        key="spo2",
        group="health",
        source_method=None,
        verified=True,
        note=(
            "Measured, not assumed: on 2026-10-09 get_spo2_data returned a full "
            "envelope with averageSpO2, lowestSpO2, avgSleepSpO2 and every "
            "series entry null. Its eight populated fields were all window "
            "timestamps."
        ),
    ),
)

BY_KEY: dict[str, Metric] = {m.key: m for m in METRICS}
KEYS: tuple[str, ...] = tuple(m.key for m in METRICS)


@dataclass(frozen=True)
class SourceCall:
    """One request to make. Distinct by (method, call), which is what makes
    three metrics out of `training_status` cost one request."""

    method: str
    call: str


def keys_for_group(group: str) -> tuple[str, ...]:
    return tuple(m.key for m in METRICS if m.group == group)


def resolve(keys: list[str] | tuple[str, ...]) -> tuple[Metric, ...]:
    """Map keys to rows, in the order asked. Raises `KeyError` on an unknown
    key -- the caller turns that into a message naming the valid ones, the way
    `probe --explain` does."""
    return tuple(BY_KEY[key] for key in keys)


def sources_for(keys: list[str] | tuple[str, ...]) -> tuple[SourceCall, ...]:
    """The distinct requests needed to answer these metrics.

    This function is the whole reason v1.5 could ship the cache *seam* without
    the store: the duplication a cache was supposed to absorb is duplication
    inside a single call, and it is removed here, before any request is made.
    Rows with no source contribute nothing -- they are answered from the map.
    """
    seen: list[SourceCall] = []
    for metric in resolve(keys):
        if metric.source_method is None:
            continue
        call = SourceCall(metric.source_method, metric.call)
        if call not in seen:
            seen.append(call)
    return tuple(seen)
