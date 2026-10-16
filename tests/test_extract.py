"""Each test here is a payload shape this project has actually received (§5).

Pure functions over literal dicts: no client, no tokens, no budget, no network.
That is the point of keeping decoding separate from fetching -- the four traps
that cost two probe sessions to find are now the cheapest things in the suite to
test.
"""

from garmin_mcp.source.extract import (
    MISSING,
    at_path,
    latest_by_timestamp,
    match_calendar_date,
    select_primary_device,
)


class TestAtPath:
    def test_walks_to_a_nested_reading(self):
        """VO2 max really does live three levels down (5b)."""
        payload = {"mostRecentVO2Max": {"generic": {"vo2MaxValue": 52.0}}}
        assert at_path(payload, "mostRecentVO2Max.generic.vo2MaxValue") == 52.0

    def test_a_missing_key_is_missing_not_an_exception(self):
        assert at_path({"a": {}}, "a.b.c") is MISSING

    def test_a_null_value_is_missing(self):
        """The spo2 lesson, 2026-10-09: `averageSpO2: null` is not a reading,
        and a counter that treated a present key as data wrote `has_data` for a
        watch that measures none. `mostRecentVO2Max.cycling` is null on this
        account for the same reason -- running only."""
        assert at_path({"mostRecentVO2Max": {"cycling": None}}, "mostRecentVO2Max.cycling") is MISSING

    def test_a_non_dict_mid_path_is_missing(self):
        assert at_path({"a": 5}, "a.b") is MISSING

    def test_an_empty_path_returns_the_payload(self):
        """How a row names a source whose whole response is the value."""
        assert at_path({"a": 1}, "") == {"a": 1}

    def test_zero_is_a_reading(self):
        """`0` is falsy and is still data -- the same trap the probe's counter
        had to be fixed for."""
        assert at_path({"recoveryTime": 0}, "recoveryTime") == 0


class TestSelectPrimaryDevice:
    """`training_status` keys its DTOs by device id (5b):
    `latestTrainingStatusData.3612870660`. The key is data, not a field name."""

    def test_selects_on_the_flag_not_on_position(self):
        dto_map = {
            "3612870660": {"primaryTrainingDevice": False, "trainingStatus": 1},
            "9999999999": {"primaryTrainingDevice": True, "trainingStatus": 3},
        }
        assert select_primary_device(dto_map)["trainingStatus"] == 3

    def test_a_hardcoded_key_would_have_been_wrong(self):
        """The same payload read positionally gives the other watch's status.
        This is the assertion the rule exists for: it would pass on this
        account today and break the day the watch is replaced."""
        dto_map = {
            "3612870660": {"primaryTrainingDevice": False, "trainingStatus": 1},
            "9999999999": {"primaryTrainingDevice": True, "trainingStatus": 3},
        }
        positional = list(dto_map.values())[0]
        assert positional is not select_primary_device(dto_map)

    def test_no_primary_is_missing_rather_than_a_guess(self):
        """Picking the only entry would answer with a secondary device's
        training status and look entirely correct."""
        assert select_primary_device({"1": {"primaryTrainingDevice": False}}) is MISSING

    def test_two_primaries_is_missing(self):
        """A shape we have never seen. Choosing either is the hardcoding
        mistake in a different costume."""
        dto_map = {
            "1": {"primaryTrainingDevice": True},
            "2": {"primaryTrainingDevice": True},
        }
        assert select_primary_device(dto_map) is MISSING

    def test_a_non_dict_is_missing(self):
        assert select_primary_device([]) is MISSING

    def test_non_dict_entries_are_ignored(self):
        dto_map = {"1": "not a dto", "2": {"primaryTrainingDevice": True, "v": 1}}
        assert select_primary_device(dto_map)["v"] == 1


class TestLatestByTimestamp:
    """`training_readiness` returns list(4) for one day (5b), and the list's
    order is documented nowhere."""

    def test_picks_the_newest_not_the_first(self):
        snaps = [
            {"timestamp": 100, "score": 40},
            {"timestamp": 900, "score": 71},
            {"timestamp": 500, "score": 55},
        ]
        assert latest_by_timestamp(snaps)["score"] == 71

    def test_an_empty_list_is_missing(self):
        assert latest_by_timestamp([]) is MISSING

    def test_a_non_list_is_missing(self):
        assert latest_by_timestamp({"timestamp": 1}) is MISSING

    def test_entries_without_a_timestamp_are_skipped(self):
        snaps = [{"score": 1}, {"timestamp": 5, "score": 2}]
        assert latest_by_timestamp(snaps)["score"] == 2

    def test_no_entry_has_a_timestamp(self):
        assert latest_by_timestamp([{"score": 1}]) is MISSING


class TestMatchCalendarDate:
    """Resting HR's DATE argument returns a statistics window, so a response
    exists for a date that has no reading."""

    def test_matches_the_date_that_was_asked_for(self):
        rows = [
            {"calendarDate": "2026-10-14", "restingHeartRate": 50},
            {"calendarDate": "2026-10-15", "restingHeartRate": 52},
        ]
        assert match_calendar_date(rows, "2026-10-15")["restingHeartRate"] == 52

    def test_a_miss_never_returns_the_nearest_value(self):
        """"No data for this date" is actionable. A neighbouring day's resting
        heart rate presented as today's is not."""
        rows = [{"calendarDate": "2026-10-14", "restingHeartRate": 50}]
        assert match_calendar_date(rows, "2026-10-15") is MISSING

    def test_a_single_dict_is_treated_as_one_row(self):
        row = {"calendarDate": "2026-10-15", "restingHeartRate": 52}
        assert match_calendar_date(row, "2026-10-15") is row

    def test_a_non_list_is_missing(self):
        assert match_calendar_date("2026-10-15", "2026-10-15") is MISSING
