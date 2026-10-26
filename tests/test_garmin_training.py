"""`garmin_training` end to end, with a fake client (§5, v1.5).

Payload shapes here are the ones recorded in the 5a/5b probe sessions, not
invented ones: the device-keyed DTOs, the `list(4)` of readiness snapshots, the
`int` code sitting beside its phrase. The point of the tool is that none of that
shape reaches the response.

No live call, no tokens that mean anything, no budget: `source._connect` is
replaced, which is the seam between everything tested here and Garmin.
"""

import pytest
from garminconnect import GarminConnectNotFoundError
from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp.server import garmin_training
from garmin_mcp.source import client as source
from garmin_mcp.source import metrics as metric_map
from garmin_mcp.source import verdicts
from garmin_mcp.source.errors import AuthExpired, RateLimited

DEVICE = "3612870660"

# recorded: 5b, 2026-10-11
TRAINING_STATUS = {
    "mostRecentVO2Max": {"generic": {"vo2MaxValue": 52.0}, "cycling": None},
    "mostRecentTrainingStatus": {
        "latestTrainingStatusData": {
            DEVICE: {
                "primaryTrainingDevice": True,
                # The int code and its phrase, side by side, exactly as 5b
                # found them. The code must never surface.
                "trainingStatus": 3,
                "trainingStatusFeedbackPhrase": "PRODUCTIVE_1",
            },
            "9999999999": {
                "primaryTrainingDevice": False,
                "trainingStatus": 7,
                "trainingStatusFeedbackPhrase": "DETRAINING_3",
            },
        }
    },
    "mostRecentTrainingLoadBalance": {
        "metricsTrainingLoadBalanceDTOMap": {
            DEVICE: {
                "primaryTrainingDevice": True,
                "trainingBalanceFeedbackPhrase": "BALANCED_2",
                "monthlyLoadAerobicLow": 120.5,
            }
        }
    },
}

# recorded: 5a/5b probe sessions -- the list(4) of snapshots
READINESS = [
    {"timestamp": 100, "score": 40, "recoveryTime": 30},
    {"timestamp": 900, "score": 71, "recoveryTime": 6},
    {"timestamp": 500, "score": 55, "recoveryTime": 18},
]

# recorded: 5a/5b probe sessions
RACE = {"time5K": 1320, "time10K": 2760, "timeHalfMarathon": 6100, "timeMarathon": 13000}


class FakeApi:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.raises: dict[str, Exception] = {}
        self.payloads = {
            "get_training_status": TRAINING_STATUS,
            "get_training_readiness": READINESS,
            "get_race_predictions": RACE,
        }

    def _record(self, name, *args):
        self.calls.append((name, args))
        if name in self.raises:
            raise self.raises[name]
        return self.payloads[name]

    def get_training_status(self, date):
        return self._record("get_training_status", date)

    def get_training_readiness(self, date):
        return self._record("get_training_readiness", date)

    def get_race_predictions(self):
        return self._record("get_race_predictions")


@pytest.fixture
def api(monkeypatch, tokenfile):
    fake = FakeApi()
    monkeypatch.setattr(source, "_connect", lambda: fake)
    return fake


def _by_metric(result):
    return {r.metric: r for r in result.readings}


def test_three_metrics_cost_one_request(api):
    """The whole argument for the seam, asserted at the tool boundary."""
    garmin_training(["training_status", "load_balance", "vo2_max"], "2026-10-15")
    assert len(api.calls) == 1


def test_a_reading_names_its_source_unit_and_date(api):
    """§5: never a bare number. Two sources can disagree, so a value whose
    origin is unstated is a value nobody can audit."""
    result = garmin_training(["vo2_max"], "2026-10-15")
    reading = _by_metric(result)["vo2_max"]

    assert reading.value == 52.0
    assert reading.unit == "mL/kg/min"
    assert reading.source == "get_training_status"
    assert reading.measured_on == "2026-10-15"
    assert reading.verdict == verdicts.PRESENT


def test_the_phrase_is_returned_and_the_code_never_is(api):
    """`trainingStatus: 3` has no sourced meaning, so it stays opaque."""
    result = garmin_training(["training_status"], "2026-10-15")
    reading = _by_metric(result)["training_status"]

    assert reading.value == "PRODUCTIVE_1"
    assert reading.value != 3
    assert "3" not in str(reading.value)


def test_the_secondary_device_is_not_read(api):
    """Both DTOs carry a status. Reading the wrong one would look entirely
    plausible -- `DETRAINING_3` is a real answer, just not this watch's."""
    result = garmin_training(["training_status"], "2026-10-15")
    assert _by_metric(result)["training_status"].value != "DETRAINING_3"


def test_readiness_comes_from_the_newest_snapshot(api):
    """list(4) for one day, selected by timestamp rather than by index."""
    result = garmin_training(["readiness", "recovery_time"], "2026-10-15")
    readings = _by_metric(result)

    assert readings["readiness"].value == 71
    assert readings["recovery_time"].value == 6
    assert len(api.calls) == 1, "both come from one payload"


def test_race_predictions_are_four_metrics_from_one_request(api):
    result = garmin_training(
        ["race_5k", "race_10k", "race_half", "race_marathon"], "2026-10-15"
    )
    readings = _by_metric(result)

    assert readings["race_5k"].value == 1320
    assert readings["race_marathon"].value == 13000
    assert all(r.unit == "s (inferred)" for r in result.readings)
    assert api.calls == [("get_race_predictions", ())]


def test_an_unverified_row_carries_its_note_on_a_present_reading(api):
    """The guard on the 2026-10-26 change, moved here when `hrv_status` was
    verified and stopped being an example of one.

    Before that change `readings.read` attached a row's note only where there
    was NO reading, so four rows with inferred field paths returned confident
    values in silence -- while `MetricReading.note`'s own schema promised "what
    is unconfirmed". `training_status` is now the standing example: its path
    `trainingStatusFeedbackPhrase` is inferred, so a present value has to say
    so. If this test ever has no unverified row left to use, that is good news
    and it should be deleted rather than weakened.
    """
    reading = _by_metric(garmin_training(["training_status"], "2026-10-15"))[
        "training_status"
    ]

    assert reading.verdict == verdicts.PRESENT
    assert reading.value == "PRODUCTIVE_1"
    assert reading.note, "an inferred path must say so even when it answers"
    assert not metric_map.BY_KEY["training_status"].verified, (
        "this test is about an unverified row; if the row was verified, move "
        "the guard to another one rather than deleting it"
    )


def test_a_missing_path_is_no_data_and_says_why(api):
    """A day the watch was not worn. Not a failure, and not silence."""
    api.payloads["get_training_status"] = {"mostRecentVO2Max": {}}
    result = garmin_training(["vo2_max"], "2026-10-15")
    reading = _by_metric(result)["vo2_max"]

    assert reading.verdict == verdicts.NO_DATA
    assert reading.value is None
    assert reading.note


def test_a_wrong_path_is_error_not_no_data(api):
    """Half the map's rows have inferred paths. One landing on a string where a
    number belongs is OUR bug, and `error` is this vocabulary's word for that.
    Called `no_data` it would be indistinguishable from an unworn watch, and the
    two need opposite responses."""
    api.payloads["get_training_status"] = {
        "mostRecentVO2Max": {"generic": {"vo2MaxValue": "fifty-two"}}
    }
    result = garmin_training(["vo2_max"], "2026-10-15")
    reading = _by_metric(result)["vo2_max"]

    assert reading.verdict == verdicts.ERROR
    assert "not a number" in reading.note


def test_a_404_on_one_source_leaves_the_others_answering(api):
    api.raises["get_race_predictions"] = GarminConnectNotFoundError("404")
    result = garmin_training(["vo2_max", "race_5k"], "2026-10-15")
    readings = _by_metric(result)

    assert readings["race_5k"].verdict == verdicts.UNSUPPORTED
    assert readings["vo2_max"].verdict == verdicts.PRESENT


def test_an_unknown_metric_names_the_valid_ones(api):
    """What `probe --explain` does: a refusal that does not say what would work
    costs the agent another round trip."""
    with pytest.raises(ToolError) as excinfo:
        garmin_training(["vo2max"], "2026-10-15")

    assert "vo2max" in str(excinfo.value)
    assert "vo2_max" in str(excinfo.value)


def test_a_health_metric_is_refused_by_the_training_tool(api):
    """`spo2` is a real row in the map, so `BY_KEY` would resolve it happily.
    The tool's own group check is what stops it."""
    with pytest.raises(ToolError):
        garmin_training(["spo2"], "2026-10-15")


def test_an_empty_request_is_refused(api):
    with pytest.raises(ToolError):
        garmin_training([], "2026-10-15")


def test_an_expired_token_surfaces_as_a_tool_error(monkeypatch, tokenfile):
    def boom():
        raise AuthExpired(str(tokenfile))

    monkeypatch.setattr(source, "_connect", boom)
    with pytest.raises(ToolError) as excinfo:
        garmin_training(["vo2_max"], "2026-10-15")

    assert "garmin_auth_status" in str(excinfo.value).lower() or "bootstrap" in str(
        excinfo.value
    ).lower()


def test_a_rate_limit_surfaces_as_a_tool_error_telling_the_agent_not_to_retry(api):
    """§6: an agent that reads a 429 as transient retries into the block."""
    api.raises["get_training_status"] = RateLimited()
    with pytest.raises(ToolError) as excinfo:
        garmin_training(["vo2_max"], "2026-10-15")

    assert "do not retry" in str(excinfo.value).lower()


def test_the_date_defaults_to_today_and_the_response_says_what_that_means(api):
    """§5: Garmin's dates are device-local and Mahi travels, so a response that
    does not state its basis is a date nobody can place."""
    from datetime import UTC, datetime

    result = garmin_training(["vo2_max"])

    assert result.date == datetime.now(UTC).date().isoformat()
    assert "device-local" in result.date_basis
    assert api.calls[0][1] == (result.date,)


def test_an_explicit_date_reaches_the_source(api):
    garmin_training(["vo2_max"], "2026-09-01")
    assert api.calls[0][1] == ("2026-09-01",)
