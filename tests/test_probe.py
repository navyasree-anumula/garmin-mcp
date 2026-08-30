"""The probe decides which tools get built, so its judgements are load-bearing.

Two failure modes matter more than the rest:

- Calling a day with no data "unsupported" would cut a metric the watch does
  produce. Garmin answers an unworn day with a well-formed envelope -- the date
  echoed back and every measurement null -- so `bool(payload)` is not merely
  imprecise, it is inverted.
- Printing or storing what it read would put health data in a terminal and a
  JSON file for the sake of a diagnostic (§8).
"""

import json

import pytest

from garmin_mcp.source import probe
from garmin_mcp.source.probe import (
    CANDIDATES,
    EMPTY,
    ERROR,
    HAS_DATA,
    NOT_FOUND,
    Result,
    count_populated,
    write_capabilities,
)


class TestEmptinessHeuristic:
    def test_echoed_request_fields_do_not_count_as_data(self):
        """The exact shape Garmin returns for a day the watch was not worn."""
        unworn = {
            "userProfilePK": 12345,
            "calendarDate": "2026-08-29",
            "startTimestampGMT": None,
            "restingHeartRate": None,
        }

        assert count_populated(unworn) == 0

    def test_real_measurements_count(self):
        worn = {"calendarDate": "2026-08-29", "restingHeartRate": 52}

        assert count_populated(worn) == 1

    def test_nested_payloads_are_walked(self):
        assert count_populated({"dto": {"score": 82, "stages": {"deep": 61}}}) == 2

    def test_empty_containers_are_empty(self):
        for value in ({}, [], None, "", {"a": None}, {"a": {}}, [[], {}]):
            assert count_populated(value) == 0, value

    def test_zero_is_data_not_absence(self):
        """A zero-step day is a measurement. Treating falsy as absent would
        silently discard real readings."""
        assert count_populated({"steps": 0}) == 1
        assert count_populated({"worn": False}) == 1

    def test_recursion_is_bounded(self):
        """A self-referential payload must not hang the probe."""
        deep = current = {}
        for _ in range(50):
            current["next"] = {}
            current = current["next"]
        current["value"] = 1

        assert count_populated(deep) == 0  # bottomed out, did not recurse forever


class FakeApi:
    def __init__(self, responses, errors=None):
        self.responses = responses
        self.errors = errors or {}
        self.calls = []

    def __getattr__(self, name):
        def method(*args):
            self.calls.append((name, args))
            if name in self.errors:
                raise self.errors[name]
            return self.responses.get(name, {})

        return method


@pytest.fixture
def patched(monkeypatch, tmp_path):
    def install(api):
        monkeypatch.setattr(probe.source, "_connect", lambda: api)
        from contextlib import contextmanager

        @contextmanager
        def noop(_path):
            yield

        monkeypatch.setattr(probe.source, "_garmin_access", noop)
        return str(tmp_path / "garmin_tokens.json")

    return install


def test_a_populated_endpoint_is_has_data(patched):
    api = FakeApi({"get_sleep_data": {"score": 80}})
    tokens = patched(api)

    results = {r.key: r for r in probe.run(tokens, day_offsets=(1,))}

    assert results["sleep"].verdict == HAS_DATA
    assert results["sleep"].populated == 1


def test_404_is_not_supported(patched):
    from garminconnect import GarminConnectNotFoundError

    api = FakeApi({}, errors={"get_hrv_data": GarminConnectNotFoundError("404")})
    tokens = patched(api)

    results = {r.key: r for r in probe.run(tokens, day_offsets=(1,))}

    assert results["hrv"].verdict == NOT_FOUND


def test_other_failures_are_recorded_not_raised(patched):
    """A probe that dies on the first bad endpoint tells you about one endpoint."""
    api = FakeApi({}, errors={"get_spo2_data": ValueError("boom")})
    tokens = patched(api)

    results = {r.key: r for r in probe.run(tokens, day_offsets=(1,))}

    assert results["spo2"].verdict == ERROR
    assert results["spo2"].detail == "ValueError"
    assert len(results) == len(CANDIDATES)


def test_rate_limiting_aborts_the_whole_probe(patched):
    """The one exception that must stop everything. Continuing through a 429
    across 26 endpoints is exactly the retry storm §6 forbids."""
    from garmin_mcp.source.errors import RateLimited

    api = FakeApi({}, errors={"get_sleep_data": RateLimited()})
    tokens = patched(api)

    with pytest.raises(RateLimited):
        probe.run(tokens, day_offsets=(1,))


def test_an_empty_day_is_retried_on_later_dates(patched):
    """The false-negative guard: one unworn day must not condemn a metric."""
    calls = {"n": 0}

    class Flaky(FakeApi):
        def __getattr__(self, name):
            def method(*args):
                self.calls.append((name, args))
                if name != "get_sleep_data":
                    return {}
                calls["n"] += 1
                return {"score": 77} if calls["n"] > 1 else {"calendarDate": args[0]}

            return method

    tokens = patched(Flaky({}))

    results = {r.key: r for r in probe.run(tokens, day_offsets=(1, 3))}

    assert results["sleep"].verdict == HAS_DATA


def test_only_the_empty_endpoints_are_retried(patched):
    """Re-probing everything would multiply the request count against an account
    that has already been rate limited once today."""
    api = FakeApi({"get_sleep_data": {"score": 80}})
    tokens = patched(api)

    probe.run(tokens, day_offsets=(1, 3, 7))

    sleep_calls = [c for c in api.calls if c[0] == "get_sleep_data"]
    assert len(sleep_calls) == 1, "a satisfied endpoint was probed again"


def test_capabilities_file_contains_no_health_data(tmp_path):
    """The file is written to the token volume and read by the web UI. It must
    carry verdicts, never readings."""
    tokens = tmp_path / "garmin_tokens.json"
    results = [Result("sleep", "health", HAS_DATA, 42, "2026-08-29")]

    path = write_capabilities(str(tokens), results, ["2026-08-29"], "0.3.0")

    document = json.loads(path.read_text())
    assert document["endpoints"]["sleep"]["verdict"] == HAS_DATA
    assert document["endpoints"]["sleep"]["populated_fields"] == 42
    # A count is not a reading. No value from the payload may appear anywhere.
    assert "score" not in path.read_text()


def test_capabilities_write_is_atomic(tmp_path):
    """Two containers share this volume; a torn read must not be possible."""
    tokens = tmp_path / "garmin_tokens.json"
    write_capabilities(str(tokens), [], ["2026-08-29"], "0.3.0")

    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert not leftovers


def test_every_candidate_has_a_known_call_shape():
    """A typo in `kind` would surface as a TypeError mid-probe, after real
    requests had already been spent."""
    assert {c.kind for c in CANDIDATES} <= {"date", "range", "none", "activities"}
    assert len({c.key for c in CANDIDATES}) == len(CANDIDATES)


def test_activities_never_uses_the_paginating_helper():
    """get_activities_by_date loops up to MAX_PAGINATED_REQUESTS (2000) with no
    delay, before any cap of ours applies."""
    assert all("by_date" not in c.method for c in CANDIDATES)


def test_capabilities_write_creates_a_missing_directory(tmp_path):
    """Found by running the real command, not by a unit test.

    `/data` always exists inside the container and `tmp_path` always exists in a
    test, so both hid the fact that write_capabilities never created its parent.
    """
    tokens = tmp_path / "does-not-exist-yet" / "garmin_tokens.json"

    path = write_capabilities(str(tokens), [], ["2026-08-29"], "0.3.0")

    assert path.is_file()


def test_progress_reports_the_date_actually_probed(patched):
    """An empty retry keeps the stored result, so reporting the stored one would
    print the first date beside a probe of a later date."""
    api = FakeApi({})  # everything empty, so everything is retried
    tokens = patched(api)
    seen = []

    probe.run(tokens, day_offsets=(1, 3), on_progress=seen.append)

    days = {r.day for r in seen if r.key == "sleep"}
    assert len(days) == 2, f"both probed dates should be reported, got {days}"
