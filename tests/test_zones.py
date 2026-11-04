"""`garmin_zones` end to end, with a fake client (§5, v1.8-B).

The payload shape here is measured; the numbers are not. Garmin stores zone
FLOORS, so every ceiling this module reports is derived -- and the tests that
matter are the ones where it **cannot** derive one, because a fabricated bound
becomes a wrong heart-rate target in v1.9 and a wrong number on a watch.

No live call and no budget: `source._connect` and `source._garmin_access` are
replaced, which is the seam between everything here and Garmin.
"""

from contextlib import contextmanager

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp.server import garmin_zones
from garmin_mcp.source import client as source
from garmin_mcp.source import zones as source_zones
from garmin_mcp.source.errors import AuthExpired

# recorded in part: probe --explain heart_rate_zones, 2026-11-04 -- the KEY
# NAMES and TYPES are measured (five `zoneNFloor` ints, `maxHeartRateUsed`,
# `lactateThresholdHeartRateUsed`, `restingHeartRateUsed` ints,
# `trainingMethod`, `sport`, `changeState` strs, `restingHrAutoUpdateUsed`
# bool), the VALUES are invented. The root is list(1) on this account, which is
# why nothing in the module assumes the profile it wants is first.
ZONES = [
    {
        "trainingMethod": "PERCENT_MAX_HR",
        "restingHeartRateUsed": 52,
        "lactateThresholdHeartRateUsed": 170,
        "zone1Floor": 95,
        "zone2Floor": 114,
        "zone3Floor": 133,
        "zone4Floor": 152,
        "zone5Floor": 171,
        "maxHeartRateUsed": 190,
        "restingHrAutoUpdateUsed": True,
        "sport": "RUNNING",
        "changeState": "NONE",
    }
]


class FakeApi:
    def __init__(self, payload=None, raises=None):
        self.payload = ZONES if payload is None else payload
        self.raises = raises
        self.calls = 0

    def get_heart_rate_zones(self):
        self.calls += 1
        if self.raises:
            raise self.raises
        return self.payload


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()

    @contextmanager
    def _no_doorway(_path):
        yield

    monkeypatch.setattr(source, "_connect", lambda: fake)
    monkeypatch.setattr(source, "_garmin_access", _no_doorway)
    monkeypatch.setattr(source, "tokenstore_path", lambda: "/tmp/not-a-token")
    return fake


def _zones(result, index=0):
    return {z.zone: z for z in result.profiles[index].zones}


def test_zone_floors_pair_into_ranges(api):
    result = garmin_zones()
    zones = _zones(result)

    assert zones[1].floor_bpm == 95
    assert zones[1].ceiling_bpm == 114, "zone 1 ends where zone 2 begins"
    assert zones[4].ceiling_bpm == 171
    assert "next zone's floor" in zones[1].ceiling_basis


def test_zone_five_ceiling_is_max_hr_and_says_it_is_inferred(api):
    """The one inference in the module, and it is labelled in the response
    rather than only in a document."""
    zone5 = _zones(garmin_zones())[5]

    assert zone5.ceiling_bpm == 190
    assert "INFERRED" in zone5.ceiling_basis


def test_a_missing_floor_leaves_its_neighbour_without_a_ceiling(api):
    """A gap must not silently widen the zone below it: zone 2 would otherwise
    appear to run to zone 4's floor, which is a range Garmin never configured."""
    gapped = {k: v for k, v in ZONES[0].items() if k != "zone3Floor"}
    api.payload = [gapped]

    zones = _zones(garmin_zones())

    assert 3 not in zones, "a floor that is not there is not a zone"
    assert zones[2].ceiling_bpm is None
    assert "missing" in zones[2].ceiling_basis


def test_a_floor_that_does_not_ascend_is_refused(api):
    api.payload = [{**ZONES[0], "zone3Floor": 100}]

    zones = _zones(garmin_zones())

    assert zones[2].ceiling_bpm is None
    assert zones[3].floor_bpm == 100, "the floor is still reported, as measured"


def test_a_missing_max_hr_leaves_zone_five_open(api):
    """A missing ceiling is an answer. A fabricated one is a wrong target."""
    api.payload = [{k: v for k, v in ZONES[0].items() if k != "maxHeartRateUsed"}]

    result = garmin_zones()

    assert result.profiles[0].max_hr_bpm is None
    assert _zones(result)[5].ceiling_bpm is None


def test_an_empty_profile_list_is_reported_not_assumed(api):
    """Nothing can be converted against an empty table, so the tool says so
    instead of returning a confident absence of zones."""
    api.payload = []

    result = garmin_zones()

    assert result.profiles == []
    assert "no zone profile" in result.note


def test_one_request_and_it_says_so(api):
    result = garmin_zones()

    assert api.calls == 1
    assert result.requests_made == 1


def test_the_response_carries_sport_and_training_method(api):
    """`training_method` has no consumer until v1.9, and it is returned anyway:
    the same zone number means different bpm under a different method, so the
    converter is wrong without it."""
    profile = garmin_zones().profiles[0]

    assert profile.sport == "RUNNING"
    assert profile.training_method == "PERCENT_MAX_HR"
    assert profile.threshold_hr_bpm == 170
    assert profile.resting_hr_used_bpm == 52


def test_a_single_dict_is_tolerated(api):
    """Never observed; cheaper to handle than to debug from a conversation."""
    api.payload = ZONES[0]

    assert len(garmin_zones().profiles) == 1


def test_a_true_is_not_a_heart_rate(api):
    """`isinstance(True, int)` is True in Python, and 1 bpm would read as a
    measurement."""
    api.payload = [{**ZONES[0], "maxHeartRateUsed": True}]

    assert garmin_zones().profiles[0].max_hr_bpm is None


def test_a_source_error_becomes_a_tool_error(api):
    api.raises = AuthExpired("/tmp/not-a-token")

    with pytest.raises(ToolError):
        garmin_zones()


def test_the_note_says_these_numbers_have_no_date(api):
    """The reason zones are not metric rows, carried where the agent reads it."""
    assert "no date" in garmin_zones().note


def test_the_module_returns_a_plain_dataclass():
    """`source/` does not know MCP exists -- the AuthStatus precedent (§11)."""
    assert not hasattr(source_zones.ZoneReading, "model_fields")
