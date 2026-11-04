"""`garmin_activities` end to end, with a fake client (§5, v1.8-A).

**The privacy tests here have a positive half, and that is the point.** An
assertion that no coordinate leaves passes vacuously against the fixture step 0
produced, because the activity it read was a strength session with no GPS at
all. So the fixture below carries synthetic coordinates, a synthetic location
name and a synthetic owner GUID, and the assertion is that none of them come
out -- the lesson the cassette scrubber (§8) was written down for.

No live call and no budget: `source._connect` and `source._garmin_access` are
replaced, which is the seam between everything here and Garmin.
"""

from contextlib import contextmanager

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp.server import garmin_activities
from garmin_mcp.source import activities as source_activities
from garmin_mcp.source import client as source

# recorded in part: probe --explain activities, 2026-11-04 -- the KEY NAMES and
# TYPES are measured off a real row (all 100+ of them; the ones this projection
# reads are reproduced here). The VALUES are invented.
#
# The location keys below are NOT measured: the probed activity was indoors and
# carried none. They are here deliberately as the positive half of the privacy
# assertion -- a GPS run is expected to carry them, and this fixture is how the
# projection is proven to drop them before one is ever seen.
RUN = {
    "activityId": 1746821178,
    "activityName": "6x800 pace",
    "startTimeLocal": "2026-11-01 08:14:03",
    "startTimeGMT": "2026-11-01 07:14:03",
    "activityType": {"typeKey": "running", "typeId": 1, "parentTypeId": 17},
    "distance": 9400.0,
    "duration": 2820.0,
    "movingDuration": 2780.0,
    "averageSpeed": 3.3333333,
    "averageHR": 148.0,
    "maxHR": 171.0,
    "aerobicTrainingEffect": 3.4,
    "anaerobicTrainingEffect": 1.1,
    "trainingEffectLabel": "TEMPO",
    "activityTrainingLoad": 142.5,
    "hrTimeInZone_1": 300.0,
    "hrTimeInZone_2": 900.0,
    "hrTimeInZone_3": 1200.0,
    "hrTimeInZone_4": 360.0,
    "hrTimeInZone_5": 60.0,
    "workoutId": 1746821178,
    "lapCount": 14,
    "hasSplits": True,
    # --- none of the following may ever appear in a response ---
    "startLatitude": 52.3702157,
    "startLongitude": 4.8951679,
    "endLatitude": 52.3680000,
    "endLongitude": 4.8900000,
    "locationName": "Vondelpark",
    "ownerId": 999001,
    "ownerDisplayName": "0fa1c0de-0000-4000-8000-000000000000",
    "ownerFullName": "A Real Person",
    "ownerProfileImageUrlSmall": "https://example.invalid/s.png",
    "ownerProfileImageUrlMedium": "https://example.invalid/m.png",
    "ownerProfileImageUrlLarge": "https://example.invalid/l.png",
    "userRoles": ["ROLE_CONNECTUSER", "ROLE_FITNESS_USER"],
    "privacy": {"typeId": 2, "typeKey": "private"},
    "deviceId": 3612870660,
    "timeZoneId": 124,
    "hasPolyline": True,
    "hasHeatMap": True,
}

# recorded in part: probe --explain activities, 2026-11-04 -- the strength shape
# is the one step 0 actually read (summarizedExerciseSets, totalReps,
# activeSets); values invented. No distance, no usable speed: the case where a
# pace must be absent rather than zero.
STRENGTH = {
    "activityId": 1746822994,
    "activityName": "circuit + warmup",
    "startTimeLocal": "2026-11-01 17:02:00",
    "activityType": {"typeKey": "strength_training", "typeId": 13},
    "distance": 0.0,
    "duration": 1800.0,
    "averageSpeed": 0.0,
    "averageHR": 118.0,
    "maxHR": 149.0,
    "workoutId": 0,
    "summarizedExerciseSets": [
        {
            "category": "SQUAT",
            "reps": 36,
            "volume": 180000,
            "duration": 240.0,
            "sets": 4,
            "maxWeight": 5000,
        }
    ],
    "totalSets": 12,
    "activeSets": 12,
    "totalReps": 108,
    "lapCount": 1,
    "hasSplits": False,
}


class FakeApi:
    def __init__(self):
        self.payload = [RUN, STRENGTH]
        self.calls: list[tuple] = []

    def get_activities_by_date(self, start, end, sport=None):
        self.calls.append((start, end, sport))
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


def _by_name(result):
    return {a.name: a for a in result.activities}


# ---- the window --------------------------------------------------------


def test_the_window_is_refused_when_the_dates_are_backwards(api):
    with pytest.raises(ToolError, match="backwards"):
        garmin_activities("2026-11-03", "2026-10-28")

    assert api.calls == [], "refused before any request"


def test_a_window_wider_than_the_cap_is_refused_and_names_it(api):
    with pytest.raises(ToolError, match="at most 31"):
        garmin_activities("2026-01-01", "2026-06-01")

    assert api.calls == []


def test_a_malformed_date_is_refused_before_any_request(api):
    with pytest.raises(ToolError):
        garmin_activities("last tuesday", "2026-11-03")

    assert api.calls == []


def test_the_whole_cap_is_allowed(api):
    """31 days inclusive, not 30. An off-by-one here would refuse a legitimate
    month-long review window."""
    garmin_activities("2026-10-04", "2026-11-03")

    assert api.calls == [("2026-10-04", "2026-11-03", None)]


def test_an_empty_window_is_a_note_not_an_error(api):
    """A week with nothing recorded reads exactly like a week the watch was not
    worn, and both are answers."""
    api.payload = []

    result = garmin_activities("2026-10-28", "2026-11-03")

    assert result.activities == []
    assert "not a failure" in result.note


# ---- privacy: the dimension this session exists for --------------------


def test_no_coordinate_or_owner_field_survives_the_projection(api):
    """The positive half. Every forbidden value goes IN through the real
    projection, and the assertion is that none of them come out -- as a value
    anywhere in the serialised response, not merely as a named attribute."""
    result = garmin_activities("2026-10-28", "2026-11-03")
    rendered = result.model_dump_json()

    for forbidden in (
        "52.3702157",
        "4.8951679",
        "52.368",
        "Vondelpark",
        "0fa1c0de-0000-4000-8000-000000000000",
        "A Real Person",
        "example.invalid",
        "ROLE_CONNECTUSER",
        "3612870660",
        "999001",
    ):
        assert forbidden not in rendered, f"{forbidden!r} reached the response"


def test_every_named_forbidden_key_is_absent_from_the_row(api):
    """Asserted against the module's own list of measured-present keys, so the
    prose cannot drift from the behaviour."""
    row = _by_name(garmin_activities("2026-10-28", "2026-11-03"))["6x800 pace"]
    fields = set(row.model_dump())

    for key in source_activities.NEVER_RETURNED:
        assert key not in fields


def test_an_unknown_field_is_dropped_rather_than_passed_through(api):
    """Allowlist by construction: this is what makes the tool safe against a
    field Garmin adds tomorrow, and against a GPS row nobody has seen yet."""
    api.payload = [{**RUN, "someFieldInventedNextYear": "surprise"}]

    rendered = garmin_activities("2026-10-28", "2026-11-03").model_dump_json()

    assert "surprise" not in rendered
    assert "someFieldInventedNextYear" not in rendered


def test_the_activity_name_is_never_logged(api, caplog):
    """A name is user-written text and can carry a place -- 'morning run in the
    park'. Counts only."""
    with caplog.at_level("INFO"):
        garmin_activities("2026-10-28", "2026-11-03")

    assert "6x800 pace" not in caplog.text
    assert "Vondelpark" not in caplog.text
    assert "2 row(s)" in caplog.text


# ---- the fields themselves ---------------------------------------------


def test_planned_and_done_are_linked_by_workout_id(api):
    """The whole point of the session: `workout_id` is what makes a plan and a
    week of training comparable."""
    assert _by_name(garmin_activities("2026-10-28", "2026-11-03"))[
        "6x800 pace"
    ].workout_id == 1746821178


def test_a_zero_workout_id_is_not_an_id(api):
    """§16.2a in a new place: a zero read as an id points at a workout that does
    not exist, and a fallback is only safe while every value means the same
    thing."""
    assert _by_name(garmin_activities("2026-10-28", "2026-11-03"))[
        "circuit + warmup"
    ].workout_id is None


def test_pace_is_derived_from_garmins_own_speed(api):
    """3.3333 m/s is 5:00/km. One converter, shared with the composer, so the
    rounding cannot differ between a pace prescribed and a pace run."""
    assert _by_name(garmin_activities("2026-10-28", "2026-11-03"))[
        "6x800 pace"
    ].avg_pace_min_km == "5:00"


def test_a_strength_activity_has_no_pace_rather_than_a_zero(api):
    """`0:00` is a pace. No pace is the truth."""
    assert _by_name(garmin_activities("2026-10-28", "2026-11-03"))[
        "circuit + warmup"
    ].avg_pace_min_km is None


def test_no_pace_band_is_applied_to_something_that_happened(api):
    """`parse_pace_min_km` refuses outside 150-720 s/km because that is a typo
    in a PRESCRIPTION. A 9:00/km walk happened and must still render."""
    api.payload = [{**RUN, "averageSpeed": 1.8518, "distance": 5000.0}]

    assert garmin_activities("2026-10-28", "2026-11-03").activities[
        0
    ].avg_pace_min_km == "9:00"


def test_partial_hr_zones_are_not_padded_with_zeros(api):
    """A zero is the claim 'no time in that zone'. An absent key means Garmin
    did not say, and 'was the easy run easy' needs them told apart."""
    api.payload = [
        {k: v for k, v in RUN.items() if k not in ("hrTimeInZone_4", "hrTimeInZone_5")}
    ]

    zones = garmin_activities("2026-10-28", "2026-11-03").activities[0].hr_time_in_zone_s

    assert sorted(zones) == [1, 2, 3]
    assert 4 not in zones


def test_hr_time_in_zone_answers_the_easy_run_question(api):
    """The field that makes session A answer item 3's purpose, two sessions
    before the zone table or any target exists."""
    zones = _by_name(garmin_activities("2026-10-28", "2026-11-03"))[
        "6x800 pace"
    ].hr_time_in_zone_s

    assert zones[3] == 1200.0
    assert sum(zones.values()) == 2820.0


def test_a_strength_session_reports_what_it_actually_did(api):
    """Garmin reports reps and weight even though v1.6 prescribes by time, and
    `maxWeight` is GRAMS upstream -- named `_g` rather than quietly divided."""
    sets = _by_name(garmin_activities("2026-10-28", "2026-11-03"))[
        "circuit + warmup"
    ].exercise_sets

    assert len(sets) == 1
    assert sets[0].category == "SQUAT"
    assert sets[0].reps == 36
    assert sets[0].max_weight_g == 5000


def test_a_row_without_an_id_is_dropped_not_invented(api):
    """An activity that cannot be named cannot be asked about later."""
    api.payload = [{k: v for k, v in RUN.items() if k != "activityId"}, STRENGTH]

    result = garmin_activities("2026-10-28", "2026-11-03")

    assert [a.name for a in result.activities] == ["circuit + warmup"]


def test_a_true_is_not_a_heart_rate(api):
    api.payload = [{**RUN, "averageHR": True}]

    assert garmin_activities("2026-10-28", "2026-11-03").activities[0].avg_hr_bpm is None


# ---- the request count -------------------------------------------------


def test_the_request_count_says_it_is_derived(api):
    """The library pages 20 per request INSIDE one call, so the count cannot be
    observed -- and `garmin_zones` reports a measured 1 under the same field
    name. A field that silently means two things is worse than no field."""
    result = garmin_activities("2026-10-28", "2026-11-03")

    assert result.requests_made == 1
    assert "derived" in result.requests_basis


def test_a_second_page_is_reflected_in_the_derived_count(api):
    api.payload = [{**RUN, "activityId": 1000 + n} for n in range(25)]

    assert garmin_activities("2026-10-04", "2026-11-03").requests_made == 2


def test_the_sport_filter_is_passed_through(api):
    garmin_activities("2026-10-28", "2026-11-03", "running")

    assert api.calls == [("2026-10-28", "2026-11-03", "running")]


def test_the_module_returns_a_plain_dataclass():
    """`source/` does not know MCP exists (§11, the AuthStatus precedent)."""
    assert not hasattr(source_activities.ActivityList, "model_fields")
