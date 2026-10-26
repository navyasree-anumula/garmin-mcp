"""`garmin_health` end to end, with a fake client (§5, v1.5).

The payload shapes here are the ones 5b actually recorded on 2026-10-11, not
invented ones: HRV's own summary -- which replaced 5a's instruction to mine the
same numbers out of the sleep payload -- and resting HR arriving as a
*statistics window* rather than as one day's reading.

That window is the case worth testing hardest. `get_rhr_day` answers a date
argument with several dated rows, so a response exists for a date that has no
reading at all. Reading `[0]` would return a neighbouring day's resting heart
rate wearing today's date, which is a worse failure than returning nothing:
nothing is an answer a coach can act on.

No live call, no budget: `source._connect` is replaced, which is the seam
between everything here and Garmin.
"""

import pytest
from garminconnect import GarminConnectNotFoundError
from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp.server import garmin_health
from garmin_mcp.source import client as source
from garmin_mcp.source import metrics as metric_map
from garmin_mcp.source import verdicts
from garmin_mcp.source.errors import RateLimited

DATE = "2026-10-15"

# 5b: 320 populated fields. Trimmed to the ones the map names plus the two it
# deliberately does not read, because both have to be present for the choice
# between them to mean anything.
HRV = {
    "hrvSummary": {
        "lastNightAvg": 42,
        "weeklyAvg": 45,
        "lastNight5MinHigh": 58,
        "baseline": {
            "lowUpper": 36,
            "balancedLow": 39,
            "balancedUpper": 52,
            "markerValue": 0.45,
        },
        "status": "BALANCED",
        "feedbackPhrase": "HRV_BALANCED_7",
    },
    "hrvReadings": [{"hrvValue": 40, "readingTimeGMT": "2026-10-15T02:11:00.0"}],
}

# The statistics window. Two dated rows, and the one that was asked for is NOT
# first -- which is the whole point of matching on `calendarDate`.
RHR = {
    "allMetrics": {
        "metricsMap": {
            "WELLNESS_RESTING_HEART_RATE": [
                {"value": 48.0, "calendarDate": "2026-10-14"},
                {"value": 51.0, "calendarDate": DATE},
            ]
        }
    }
}


# 3584 populated fields on 2026-10-25, trimmed to the paths the map names plus
# the ones it deliberately refuses. The root answering for HRV and resting heart
# rate is the interesting part: sleep is a real second source for both.
SLEEP = {
    "dailySleepDTO": {
        "calendarDate": DATE,
        "sleepTimeSeconds": 25380,
        "napTimeSeconds": 0,
        "deepSleepSeconds": 4620,
        "remSleepSeconds": 5400,
        "avgHeartRate": 54.0,
        "sleepScores": {
            "overall": {"value": 76, "qualifierKey": "FAIR"},
            # Four of the eight sub-objects carry no `value` at all, only a
            # qualifier and an optimal range. Mapping one of those would have
            # produced a confident `no_data` forever.
            "totalDuration": {
                "qualifierKey": "GOOD",
                "optimalStart": 28800.0,
                "optimalEnd": 32400.0,
            },
            "remPercentage": {"value": 21, "qualifierKey": "GOOD"},
        },
    },
    "avgOvernightHrv": 41.0,
    "hrvStatus": "BALANCED",
    "restingHeartRate": 49,
    "avgSkinTempDeviationC": -0.2,
}

# The descriptor-driven positional arrays v1.4 predicted, measured at last. The
# scalars at the root are why stress needs no decoder to ship.
STRESS = {
    "calendarDate": DATE,
    "avgStressLevel": 27,
    "maxStressLevel": 91,
    "stressValueDescriptorsDTOList": [
        {"index": 0, "key": "timestamp"},
        {"index": 1, "key": "stressLevel"},
    ],
    "stressValuesArray": [[1761350400000, 24], [1761350580000, 31]],
    "bodyBatteryValueDescriptorsDTOList": [
        {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "timestamp"},
        {"bodyBatteryValueDescriptorIndex": 2, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"},
    ],
    "bodyBatteryValuesArray": [[1761350400000, "MEASURED", 62, 1.0]],
}


class FakeApi:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.raises: dict[str, Exception] = {}
        self.payloads = {
            "get_hrv_data": HRV,
            "get_rhr_day": RHR,
            "get_sleep_data": SLEEP,
            "get_all_day_stress": STRESS,
        }

    def _record(self, name, *args):
        self.calls.append((name, args))
        if name in self.raises:
            raise self.raises[name]
        return self.payloads[name]

    def get_hrv_data(self, date):
        return self._record("get_hrv_data", date)

    def get_rhr_day(self, date):
        return self._record("get_rhr_day", date)

    def get_sleep_data(self, date):
        return self._record("get_sleep_data", date)

    def get_all_day_stress(self, date):
        return self._record("get_all_day_stress", date)


@pytest.fixture
def api(monkeypatch, tokenfile):
    fake = FakeApi()
    monkeypatch.setattr(source, "_connect", lambda: fake)
    return fake


def _by_metric(result):
    return {r.metric: r for r in result.readings}


def test_three_hrv_metrics_cost_one_request(api):
    """The seam, asserted at the tool boundary. Asking for the night, the week
    and the verdict separately would be three requests against a budget of one
    every two seconds."""
    garmin_health(["hrv_last_night", "hrv_weekly", "hrv_status"], DATE)
    assert api.calls == [("get_hrv_data", (DATE,))]


def test_the_whole_health_group_is_four_requests(api):
    """Ten metrics, four endpoints: spo2 adds none because it has no source,
    and the three HRV, two stress and three sleep rows each share a payload.
    Worth asserting as a number -- it is what §6's budget arithmetic rests on."""
    calls = metric_map.sources_for(metric_map.keys_for_group("health"))
    assert len(calls) == 4
    assert {c.method for c in calls} == {
        "get_hrv_data",
        "get_rhr_day",
        "get_sleep_data",
        "get_all_day_stress",
    }


def test_a_reading_names_its_source_unit_and_date(api):
    """§5: never a bare number."""
    reading = _by_metric(garmin_health(["hrv_last_night"], DATE))["hrv_last_night"]

    assert reading.value == 42
    assert reading.unit == "ms"
    assert reading.source == "get_hrv_data"
    assert reading.measured_on == DATE
    assert reading.verdict == verdicts.PRESENT


def test_the_weekly_average_comes_from_the_same_payload(api):
    readings = _by_metric(garmin_health(["hrv_last_night", "hrv_weekly"], DATE))

    assert readings["hrv_last_night"].value == 42
    assert readings["hrv_weekly"].value == 45
    assert len(api.calls) == 1


def test_hrv_status_is_a_phrase_with_no_unit(api):
    """Garmin's own wording. §5 forbids giving a phrase a fake unit."""
    reading = _by_metric(garmin_health(["hrv_status"], DATE))["hrv_status"]

    assert reading.value == "BALANCED"
    assert reading.unit == ""


def test_hrv_status_reads_status_and_not_the_other_candidate(api):
    """`status` and `feedbackPhrase` both exist in the payload, and until
    2026-10-26 which one held the verdict was inferred. It was settled against
    the Garmin Connect app: the app showed the same status this row returns for
    the same night. The assertion that matters is that the OTHER field is not
    what comes back -- both are strings, so a wrong pick would have looked
    entirely plausible."""
    reading = _by_metric(garmin_health(["hrv_status"], DATE))["hrv_status"]

    assert reading.value == "BALANCED"
    assert reading.value != "HRV_BALANCED_7", "the other candidate field"


def test_a_verified_row_carries_no_provenance_note(api):
    """The mirror of the training-group test below it. A verified row says
    nothing about provenance on a present reading, because a note on every
    answer is how a field stops being read -- which is the reason the note is
    attached conditionally rather than always."""
    reading = _by_metric(garmin_health(["hrv_status"], DATE))["hrv_status"]

    assert reading.note == ""


def test_resting_hr_is_matched_on_its_own_date_not_by_position(api):
    """The row for the date asked for is second in the window. Reading `[0]`
    would have returned 48.0 -- a real resting heart rate, just not this
    date's."""
    reading = _by_metric(garmin_health(["resting_hr"], DATE))["resting_hr"]

    assert reading.value == 51.0
    assert reading.value != 48.0
    assert reading.unit == "bpm"


def test_a_date_with_no_row_is_no_data_rather_than_a_neighbours_number(api):
    """The window answers for a date it has no reading for. Returning the
    nearest value would be a number nobody could audit; `no_data` is an answer
    a coach can act on."""
    reading = _by_metric(garmin_health(["resting_hr"], "2026-10-01"))["resting_hr"]

    assert reading.verdict == verdicts.NO_DATA
    assert reading.value is None
    assert reading.note


def test_spo2_is_answerable_and_reports_unsupported_without_a_request(api):
    """§5: a metric this watch cannot measure is reported, never removed, so
    "this watch does not measure SpO2" stays an answer the agent can give."""
    reading = _by_metric(garmin_health(["spo2"], DATE))["spo2"]

    assert reading.verdict == verdicts.UNSUPPORTED
    assert reading.value is None
    assert reading.note, "an absence has to say why"
    assert api.calls == [], "a no-source row must not cost a request"


def test_a_wrong_path_is_error_not_no_data(api):
    """An inferred path landing on the wrong type is OUR map being wrong. Called
    `no_data` it would be indistinguishable from an unworn night, and the two
    need opposite responses: a one-line map fix versus nothing at all."""
    api.payloads["get_hrv_data"] = {"hrvSummary": {"lastNightAvg": "forty-two"}}
    reading = _by_metric(garmin_health(["hrv_last_night"], DATE))["hrv_last_night"]

    assert reading.verdict == verdicts.ERROR
    assert "not a number" in reading.note


def test_a_404_on_one_source_leaves_the_other_answering(api):
    api.raises["get_hrv_data"] = GarminConnectNotFoundError("404")
    readings = _by_metric(garmin_health(["hrv_last_night", "resting_hr"], DATE))

    assert readings["hrv_last_night"].verdict == verdicts.UNSUPPORTED
    assert readings["resting_hr"].verdict == verdicts.PRESENT


def test_a_training_metric_is_refused_by_the_health_tool(api):
    """`vo2_max` is a real row, so `BY_KEY` would resolve it happily. The
    group check is what stops it -- the same guard, shared with the training
    tool so the two cannot drift apart."""
    with pytest.raises(ToolError):
        garmin_health(["vo2_max"], DATE)


def test_an_unknown_metric_names_the_valid_ones(api):
    with pytest.raises(ToolError) as excinfo:
        garmin_health(["hrv"], DATE)

    assert "hrv_last_night" in str(excinfo.value)


def test_an_empty_request_is_refused(api):
    with pytest.raises(ToolError):
        garmin_health([], DATE)


def test_a_rate_limit_tells_the_agent_not_to_retry(api):
    """§6: an agent that reads a 429 as transient retries into the block."""
    api.raises["get_hrv_data"] = RateLimited()
    with pytest.raises(ToolError) as excinfo:
        garmin_health(["hrv_last_night"], DATE)

    assert "do not retry" in str(excinfo.value).lower()


def test_the_date_defaults_to_today_and_the_response_says_what_that_means(api):
    from datetime import UTC, datetime

    result = garmin_health(["hrv_last_night"])

    assert result.date == datetime.now(UTC).date().isoformat()
    assert "device-local" in result.date_basis
    assert api.calls[0][1] == (result.date,)


def test_a_verified_row_carries_no_note_on_a_present_reading(api):
    """The negative control for the test above. If every reading carried its
    row's provenance commentary, the note field would be noise and a reader
    would stop looking at it -- which is exactly when the unverified ones stop
    being read too."""
    reading = _by_metric(garmin_health(["hrv_last_night"], DATE))["hrv_last_night"]

    assert reading.verdict == verdicts.PRESENT
    assert reading.note == ""


def test_three_sleep_metrics_cost_one_request(api):
    readings = _by_metric(
        garmin_health(["sleep_score", "sleep_score_phrase", "sleep_duration"], DATE)
    )

    assert readings["sleep_score"].value == 76
    assert readings["sleep_score_phrase"].value == "FAIR"
    assert readings["sleep_duration"].value == 25380
    assert api.calls == [("get_sleep_data", (DATE,))]


def test_the_sleep_score_says_its_scale_is_unconfirmed(api):
    """§5 forbids a bare number. It also forbids a confident wrong scale: the
    shape is measured and the 0-100 range is not."""
    reading = _by_metric(garmin_health(["sleep_score"], DATE))["sleep_score"]

    assert "unconfirmed" in reading.unit


def test_sleep_duration_excludes_naps(api):
    """`napTimeSeconds` is a separate field and is not added in. A nap and a
    night are not interchangeable to a coach."""
    reading = _by_metric(garmin_health(["sleep_duration"], DATE))["sleep_duration"]

    assert reading.value == 25380


def test_resting_hr_comes_from_its_own_endpoint_not_the_sleep_payload(api):
    """Sleep's root carries `restingHeartRate: 49`, so this metric has two real
    sources. 5a said to read it off sleep; 5b withdrew that because the
    dedicated endpoint carries context sleep does not. Asserted so the choice
    cannot be quietly reversed -- and the reading names its source either way."""
    reading = _by_metric(garmin_health(["resting_hr"], DATE))["resting_hr"]

    assert reading.source == "get_rhr_day"
    assert reading.value == 51.0
    assert reading.value != 49, "that is the sleep payload's copy"
    assert ("get_sleep_data", (DATE,)) not in api.calls


def test_hrv_comes_from_its_own_endpoint_too(api):
    """Same shape of decision: sleep carries `avgOvernightHrv` and `hrvStatus`,
    but only `get_hrv_data` carries the weekly average and the baseline."""
    readings = _by_metric(garmin_health(["hrv_last_night", "hrv_status"], DATE))

    assert all(r.source == "get_hrv_data" for r in readings.values())
    assert readings["hrv_last_night"].value == 42, "not sleep's avgOvernightHrv"


def test_both_stress_scalars_cost_one_request(api):
    """An average alone hides a day that spiked and recovered."""
    readings = _by_metric(garmin_health(["stress_avg", "stress_max"], DATE))

    assert readings["stress_avg"].value == 27
    assert readings["stress_max"].value == 91
    assert api.calls == [("get_all_day_stress", (DATE,))]


def test_body_battery_is_not_offerable_until_its_decoder_exists(api):
    """It lives in `bodyBatteryValuesArray` as a descriptor-indexed positional
    array, which needs a decoder that maps a descriptor key to a position.
    Measured but unmapped, and asserted so it cannot be added by inference the
    way `lactate_threshold` is guarded in the training group."""
    assert "body_battery" not in metric_map.BY_KEY

    with pytest.raises(ToolError):
        garmin_health(["body_battery"], DATE)
