"""`--explain` answers the question a count cannot.

The probe reports how many populated leaves a payload carried. That distinguishes
"something came back" from "nothing did" -- and stops exactly where the
interesting question starts, because a zero is consistent with two opposite
situations:

    the endpoint returned an empty container      -> the watch has no such data
    it returned data our counter walked past      -> we are calling it wrong

Eight endpoints are stuck on that ambiguity, and declaring "this watch does not
produce HRV" on the strength of a zero would permanently cut a tool that works.

The constraint is that resolving it must not put readings anywhere. Key names
are schema; values are health data (docs/SCOPE.md §8).
"""

import json

import pytest

from garmin_mcp.source import probe
from garmin_mcp.source.probe import (
    CANDIDATES,
    MAX_SHAPE_LINES,
    NONE,
    describe_shape,
)


def rendered(payload, **kwargs):
    return "\n".join(describe_shape(payload, **kwargs))


class TestItNeverPrintsAValue:
    """The §8 line. Everything else in this file is secondary to it."""

    def test_no_measurement_survives_into_the_output(self):
        payload = {
            "calendarDate": "2026-08-29",
            "restingHeartRate": 52,
            "sleepScore": 83,
            "weightGrams": 71400,
            "note": "slept badly",
            "spo2": 96.5,
        }

        text = rendered(payload)

        for value in ("52", "83", "71400", "slept badly", "96.5", "2026-08-29"):
            assert value not in text, f"{value!r} leaked into the shape output"

    def test_the_key_names_do_survive(self):
        """Which is the point -- schema is what makes the extractor writable."""
        text = rendered({"restingHeartRate": 52, "sleepScore": 83})

        assert "restingHeartRate: int" in text
        assert "sleepScore: int" in text

    def test_values_nested_deep_do_not_leak(self):
        text = rendered({"dto": {"levels": [{"deepSleepSeconds": 6100}]}})

        assert "6100" not in text
        assert "dto.levels[0].deepSleepSeconds: int" in text

    def test_a_date_keyed_payload_does_not_print_the_dates(self):
        """A response keyed by day would otherwise emit one line per date, so
        the diagnostic would grow with the account rather than the schema."""
        payload = {f"2026-08-{d:02d}": {"steps": 8000 + d} for d in range(1, 15)}

        text = rendered(payload)

        assert "2026-08-01" not in text
        assert "2026-08-14" not in text
        assert "<key>" in text
        assert "14 data-shaped keys" in text


class TestTheDistinctionItExistsToDraw:
    def test_an_empty_container_and_a_walked_past_value_look_different(self):
        """The whole reason this exists. Both count as zero populated; they must
        not read the same."""
        empty_container = rendered({"hrvSummary": {}})
        has_a_reading = rendered({"hrvSummary": {"weeklyAvg": 42}})

        assert empty_container != has_a_reading
        assert "dict(empty)" in empty_container
        assert "hrvSummary.weeklyAvg: int" in has_a_reading

    def test_a_null_leaf_is_reported_not_skipped(self):
        """`restingHeartRate: null` versus the key being absent entirely is
        precisely the difference between "no reading that day" and "wrong call
        shape". Dropping nulls would discard the answer."""
        text = rendered({"restingHeartRate": None})

        assert "restingHeartRate: null" in text

    def test_an_unworn_day_is_legible_as_such(self):
        """The exact envelope Garmin returns for a day the watch sat on a
        dresser: every key present, every measurement null."""
        unworn = {
            "calendarDate": "2026-08-29",
            "restingHeartRate": None,
            "maxHeartRate": None,
            "values": [],
        }

        text = rendered(unworn)

        assert "restingHeartRate: null" in text
        assert "maxHeartRate: null" in text
        assert "values: list(0)" in text

    def test_an_absent_key_is_absent_from_the_output(self):
        text = rendered({"calendarDate": "2026-08-29"})

        assert "restingHeartRate" not in text


class TestItStaysReadable:
    def test_a_long_array_reports_its_length_and_one_element(self):
        """Stress returned 1935 populated leaves, nearly all of it one
        per-minute sample array. Describing every element would make the
        diagnostic as unreadable as the payload it explains."""
        payload = {"stressValuesArray": [[i, 30 + i % 5] for i in range(1440)]}

        lines = describe_shape(payload)

        assert "stressValuesArray: list(1440)" in "\n".join(lines)
        assert len(lines) < 10

    def test_output_is_capped(self):
        payload = {f"field{i}": i for i in range(500)}

        lines = describe_shape(payload, max_lines=25)

        assert len(lines) <= 26          # 25 plus the truncation note
        assert "capped at 25 lines" in lines[-1]

    def test_recursion_is_bounded(self):
        """A self-referential payload must not hang the diagnostic, for the same
        reason count_populated is bounded."""
        deep = current = {}
        for _ in range(50):
            current["next"] = {}
            current = current["next"]
        current["value"] = 1

        lines = describe_shape(deep)

        assert any("not descended" in line for line in lines)
        assert len(lines) < 20

    def test_bool_is_not_reported_as_int(self):
        """bool IS an int in Python. Reporting `enabled: int` would send a
        reader looking for a number."""
        text = rendered({"enabled": True, "count": 1})

        assert "enabled: bool" in text
        assert "count: int" in text

    def test_an_empty_string_is_distinguished_from_a_populated_one(self):
        text = rendered({"blank": "", "filled": "something"})

        assert "blank: str(empty)" in text
        assert "filled: str" in text
        assert "something" not in text


class TestRacePredictionsWasNeverActuallyTested:
    """The first probe run recorded an ERROR for this endpoint that was entirely
    our own doing."""

    def test_it_takes_no_arguments(self):
        """`get_race_predictions` accepts zero arguments or all three and raises
        ValueError for anything between. Classified RANGE, `_call` passed two,
        so it failed before issuing a request."""
        candidate = next(c for c in CANDIDATES if c.key == "race_predictions")

        assert candidate.kind == NONE

    def test_the_call_passes_no_arguments(self):
        seen = []

        class FakeApi:
            def get_race_predictions(self, *args):
                seen.append(args)
                return {"ok": 1}

        candidate = next(c for c in CANDIDATES if c.key == "race_predictions")
        probe._call(FakeApi(), candidate, "2026-08-29")

        assert seen == [()]

    def test_the_real_library_signature_still_rejects_two_arguments(self):
        """Pins the reason. If a future release accepts two, this fails and the
        classification can be revisited deliberately."""
        from garminconnect import Garmin

        with pytest.raises(ValueError):
            Garmin().get_race_predictions("2026-08-01", "2026-08-07")


class FakeApi:
    def __init__(self, by_day):
        self.by_day = by_day
        self.calls = []

    def __getattr__(self, name):
        def method(*args):
            self.calls.append((name, args))
            return self.by_day.get(args[0] if args else None)
        return method


class TestExplainDrivesTheRequests:
    @pytest.fixture(autouse=True)
    def _tokens(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GARMINTOKENS", str(tmp_path / "garmin_tokens.json"))

    def install(self, monkeypatch, by_day):
        api = FakeApi(by_day)
        monkeypatch.setattr(probe.source, "_connect", lambda: api)
        return api

    def test_it_stops_at_the_first_populated_date(self, monkeypatch, tmp_path):
        """One request in the common case, not three. The account has already
        been rate limited once today."""
        api = self.install(monkeypatch, {"__any__": None})
        api.by_day = {}
        monkeypatch.setattr(
            probe, "_fetch",
            lambda a, c, d, t: (probe.Result(c.key, c.group, probe.HAS_DATA, 5, d), {"x": 1}),
        )

        shapes = probe.explain(str(tmp_path / "t.json"), ["sleep"])

        assert len(shapes) == 1
        assert shapes[0].populated == 5

    def test_an_endpoint_that_stays_empty_is_still_described(self, monkeypatch, tmp_path):
        """The empty envelope is the interesting case, not a failure to report.
        Returning no lines for it would throw away the answer."""
        empty_envelope = {"calendarDate": "2026-08-29", "restingHeartRate": None}
        monkeypatch.setattr(
            probe, "_fetch",
            lambda a, c, d, t: (probe.Result(c.key, c.group, probe.EMPTY, 0, d), empty_envelope),
        )
        monkeypatch.setattr(probe.source, "_connect", lambda: object())

        shapes = probe.explain(str(tmp_path / "t.json"), ["resting_hr"])

        assert shapes[0].verdict == probe.EMPTY
        assert "restingHeartRate: null" in "\n".join(shapes[0].lines)

    def test_a_404_is_not_described_as_an_empty_payload(self, monkeypatch, tmp_path):
        """NOT_FOUND means the endpoint does not exist for this account. That is
        different from one that answered with nothing, and must not be dressed
        up as a shape."""
        monkeypatch.setattr(
            probe, "_fetch",
            lambda a, c, d, t: (probe.Result(c.key, c.group, probe.NOT_FOUND, 0, d), None),
        )
        monkeypatch.setattr(probe.source, "_connect", lambda: object())

        shapes = probe.explain(str(tmp_path / "t.json"), ["spo2"])

        assert shapes[0].verdict == probe.NOT_FOUND
        assert shapes[0].lines == []

    def test_it_writes_nothing(self, monkeypatch, tmp_path):
        """capabilities.json has one writer. A read-only diagnostic must not
        quietly become a second one."""
        monkeypatch.setattr(
            probe, "_fetch",
            lambda a, c, d, t: (probe.Result(c.key, c.group, probe.HAS_DATA, 3, d), {"a": 1}),
        )
        monkeypatch.setattr(probe.source, "_connect", lambda: object())

        probe.explain(str(tmp_path / "t.json"), ["sleep", "stress"])

        assert list(tmp_path.iterdir()) == []
