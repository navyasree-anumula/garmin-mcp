"""Find out what this account and this watch actually return (docs/SCOPE.md §5).

Body Battery, HRV status and Training Readiness are device-tier dependent, and a
spec sheet is not evidence of what the *API* returns for one account. §5 requires
the tool surface be trimmed to measured reality before it is fixed, so this runs
once and records what came back.

**It never records or prints a value.** The question is "does this endpoint
return anything for me", which is a boolean. Printing sleep scores and heart
rates into a terminal and its scrollback is precisely what §8 forbids, and a
diagnostic is a silly thing to leak health data for. Verdicts and a count of
populated fields are all that leaves this module.

**"No data" is not "not supported".** A 404 means the endpoint does not exist for
this account. A day the watch sat on a dresser returns a well-formed empty
payload from an endpoint that works perfectly. Treating those alike would cut
tools the FR570 genuinely supports, so anything that looks empty is retried on
further dates before the verdict sticks -- and only the empty ones are retried,
which keeps the request count down.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import client as source
from .errors import RateLimited

logger = logging.getLogger(__name__)

CAPABILITIES_FILENAME = "capabilities.json"

# Dates are spread rather than consecutive: three days in a row can all be days
# the watch was not worn, and that would read as "unsupported" for the whole
# health group.
DEFAULT_DAY_OFFSETS = (1, 3, 7)

# Echoed request parameters and identifiers are present even in an empty
# response, so they must not count as data.
_IGNORED_KEYS = frozenset(
    {
        "userprofilepk",
        "userprofileid",
        "calendardate",
        "date",
        "startdate",
        "enddate",
        "starttimestamplocal",
        "starttimestampgmt",
        "endtimestamplocal",
        "endtimestampgmt",
        "statisticsstartdate",
        "statisticsenddate",
        "uuid",
        "devicedid",
    }
)

DATE = "date"
RANGE = "range"
NONE = "none"
ACTIVITIES = "activities"


@dataclass(frozen=True)
class Candidate:
    key: str
    group: str
    method: str
    kind: str
    note: str = ""


CANDIDATES: tuple[Candidate, ...] = (
    # Device-tier dependent -- the whole reason this probe exists.
    Candidate("sleep", "health", "get_sleep_data", DATE),
    Candidate("hrv", "health", "get_hrv_data", DATE, "device-tier dependent"),
    Candidate("body_battery", "health", "get_body_battery", RANGE, "device-tier dependent"),
    Candidate("resting_hr", "health", "get_rhr_day", DATE),
    Candidate("stress", "health", "get_all_day_stress", DATE),
    Candidate("spo2", "health", "get_spo2_data", DATE, "device-tier dependent"),
    Candidate("respiration", "health", "get_respiration_data", DATE),
    Candidate("intensity_minutes", "health", "get_intensity_minutes_data", DATE),
    Candidate("training_status", "training", "get_training_status", DATE),
    Candidate("training_readiness", "training", "get_training_readiness", DATE, "device-tier dependent"),
    Candidate("max_metrics", "training", "get_max_metrics", DATE, "VO2 max"),
    Candidate("endurance_score", "training", "get_endurance_score", RANGE),
    Candidate("hill_score", "training", "get_hill_score", RANGE),
    Candidate("fitness_age", "training", "get_fitnessage_data", DATE),
    Candidate("race_predictions", "training", "get_race_predictions", RANGE),
    Candidate("body_composition", "body", "get_body_composition", RANGE),
    Candidate("weigh_ins", "body", "get_weigh_ins", RANGE),
    Candidate("daily_stats", "daily", "get_stats", DATE),
    Candidate("user_summary", "daily", "get_user_summary", DATE),
    Candidate("steps", "daily", "get_steps_data", DATE),
    Candidate("floors", "daily", "get_floors", DATE),
    Candidate("activities", "activity", "get_activities", ACTIVITIES),
    Candidate("activity_types", "activity", "get_activity_types", NONE),
    Candidate("personal_records", "activity", "get_personal_record", NONE),
    Candidate("devices", "device", "get_devices", NONE),
    Candidate("primary_device", "device", "get_primary_training_device", NONE),
)

HAS_DATA = "has_data"
EMPTY = "empty"
NOT_FOUND = "not_found"
ERROR = "error"


def count_populated(value: Any, _depth: int = 0) -> int:
    """How many meaningful leaves a payload carries. Never returns the leaves.

    Garmin answers a no-data day with a well-formed envelope: the date echoed
    back and every measurement null. `bool(payload)` is therefore useless -- it
    is True for a response that contains nothing at all.
    """
    if _depth > 8:
        return 0
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return 1
    if isinstance(value, (int, float, str)):
        return 1
    if isinstance(value, dict):
        return sum(
            count_populated(v, _depth + 1)
            for k, v in value.items()
            if str(k).lower() not in _IGNORED_KEYS
        )
    if isinstance(value, (list, tuple)):
        return sum(count_populated(v, _depth + 1) for v in value)
    return 0


def _call(api: Any, cand: Candidate, day: str) -> Any:
    method = getattr(api, cand.method)
    if cand.kind == DATE:
        return method(day)
    if cand.kind == RANGE:
        return method(day, day)
    if cand.kind == ACTIVITIES:
        # Never get_activities_by_date: it paginates up to MAX_PAGINATED_REQUESTS
        # (2000) with no delay, and our cap would apply only after the fact.
        return method(0, 1)
    return method()


@dataclass(frozen=True)
class Result:
    key: str
    group: str
    verdict: str
    populated: int
    day: str
    detail: str = ""


def _probe_one(api: Any, cand: Candidate, day: str, tokens_path: str) -> Result:
    from garminconnect import GarminConnectNotFoundError

    try:
        with source._garmin_access(tokens_path):
            payload = _call(api, cand, day)
    except GarminConnectNotFoundError:
        # 404 is a clean signal: this endpoint does not exist for this account.
        return Result(cand.key, cand.group, NOT_FOUND, 0, day)
    except RateLimited:
        raise
    except Exception as exc:  # noqa: BLE001 - a probe records failures, not raises
        return Result(cand.key, cand.group, ERROR, 0, day, type(exc).__name__)

    populated = count_populated(payload)
    verdict = HAS_DATA if populated else EMPTY
    return Result(cand.key, cand.group, verdict, populated, day)


def run(
    tokens_path: str,
    day_offsets: tuple[int, ...] = DEFAULT_DAY_OFFSETS,
    on_progress=None,
) -> list[Result]:
    """Probe every candidate, retrying only the empties on further dates."""
    api = source._connect()
    today = datetime.now(UTC).date()
    days = [str(today - timedelta(days=n)) for n in day_offsets]

    results: dict[str, Result] = {}
    pending = list(CANDIDATES)

    for day in days:
        if not pending:
            break
        still_empty: list[Candidate] = []
        for cand in pending:
            result = _probe_one(api, cand, day, tokens_path)
            previous = results.get(cand.key)
            # A later date only ever improves a verdict; a populated answer on
            # any date proves the endpoint works for this device.
            if previous is None or result.populated > previous.populated:
                results[cand.key] = result
            if results[cand.key].verdict == EMPTY:
                still_empty.append(cand)
            if on_progress:
                # Report what THIS pass saw, not the stored best. An empty retry
                # does not replace the stored result, so reporting the stored one
                # would print the first date beside a probe of a later one.
                on_progress(result)
        pending = still_empty

    return [results[c.key] for c in CANDIDATES if c.key in results]


def write_capabilities(
    tokens_path: str, results: list[Result], days: list[str], version: str
) -> Path:
    """Record the verdicts beside the tokens. The web UI reads this to build the
    tool-selection list (§5): the probe decides what is AVAILABLE, the operator
    decides what is EXPOSED."""
    directory = Path(tokens_path).expanduser().parent
    # /data always exists inside the container, which is exactly why this was
    # missed: the unit test used tmp_path, which also always exists. Running the
    # real command against a fresh directory is what found it.
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CAPABILITIES_FILENAME
    document = {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "server_version": version,
        "dates_probed": days,
        "endpoints": {
            r.key: {
                "group": r.group,
                "verdict": r.verdict,
                "populated_fields": r.populated,
                "observed_on": r.day,
                "detail": r.detail,
            }
            for r in results
        },
    }
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(document, indent=2))
    tmp.replace(path)
    return path
