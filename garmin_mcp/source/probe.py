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
import re
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
    # NONE, not RANGE. `get_race_predictions` accepts zero arguments or all
    # three (startdate, enddate, _type) and raises ValueError for anything
    # between -- so a RANGE call, which passes two, failed before issuing a
    # request. The first probe run recorded an ERROR for this endpoint that was
    # entirely our own: it has never actually been tested.
    Candidate("race_predictions", "training", "get_race_predictions", NONE),
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


def _fetch(
    api: Any, cand: Candidate, day: str, tokens_path: str
) -> tuple[Result, Any]:
    """One request, returning both the verdict and the payload.

    Split out from `_probe_one` so `explain` can describe the shape of what came
    back without spending a second request to see it. The payload never leaves
    this module intact -- `explain` reduces it to key paths and type names.
    """
    from garminconnect import GarminConnectNotFoundError

    try:
        with source._garmin_access(tokens_path):
            payload = _call(api, cand, day)
    except GarminConnectNotFoundError:
        # 404 is a clean signal: this endpoint does not exist for this account.
        return Result(cand.key, cand.group, NOT_FOUND, 0, day), None
    except RateLimited:
        raise
    except Exception as exc:  # noqa: BLE001 - a probe records failures, not raises
        return Result(cand.key, cand.group, ERROR, 0, day, type(exc).__name__), None

    populated = count_populated(payload)
    verdict = HAS_DATA if populated else EMPTY
    return Result(cand.key, cand.group, verdict, populated, day), payload


def _probe_one(api: Any, cand: Candidate, day: str, tokens_path: str) -> Result:
    return _fetch(api, cand, day, tokens_path)[0]


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


# ---------------------------------------------------------------------------
# Structure-only diagnostic (docs/SCOPE.md §5, §8)
# ---------------------------------------------------------------------------

# `restingHeartRate: int` establishes the shape. `restingHeartRate: 52` would be
# a reading in a terminal, its scrollback and anywhere the output gets pasted.
# Key names are schema, not health data, so describing structure stays inside §8
# while describing values would not.

MAX_SHAPE_LINES = 200
_MAX_SHAPE_DEPTH = 8

# Keys that are themselves data rather than schema: a payload keyed by date or
# epoch would otherwise print one line per day, so the diagnostic would grow with
# the size of the account instead of with the shape of the response.
_DATA_SHAPED_KEY = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ].*)?$"   # 2026-08-29, 2026-08-29T22:10:00
    r"|^\d{8,}$"                       # epoch millis
    r"|^-?\d+(\.\d+)?$"                # plain numbers
)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    # bool before int: bool IS an int in Python, and reporting `enabled: int`
    # would send whoever reads this looking for a number.
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str(empty)" if value == "" else "str"
    return type(value).__name__


def _is_data_keyed(node: dict) -> bool:
    keys = [str(k) for k in node]
    return len(keys) >= 3 and all(_DATA_SHAPED_KEY.match(k) for k in keys)


def describe_shape(payload: Any, max_lines: int = MAX_SHAPE_LINES) -> list[str]:
    """Key paths and value types. Never a value.

    Written to answer one question the counting heuristic cannot: an endpoint
    that looks empty may have returned an empty container, or may have returned
    data that `count_populated` walked past. Those have opposite conclusions --
    "this watch does not produce HRV" versus "we are calling it wrong" -- and
    are currently indistinguishable.

    So `null` leaves are REPORTED rather than skipped. `restingHeartRate: null`
    against the key being absent entirely is exactly the distinction being
    drawn, and dropping nulls would throw away the answer.
    """
    lines: list[str] = []
    truncated = False

    def walk(node: Any, path: str, depth: int) -> None:
        nonlocal truncated
        if truncated:
            return
        if len(lines) >= max_lines:
            truncated = True
            return

        label = path or "(root)"

        if isinstance(node, dict):
            if not node:
                lines.append(f"{label}: dict(empty)")
                return
            if depth >= _MAX_SHAPE_DEPTH:
                # Bounded rather than trusted: the bound is also what makes a
                # self-referential payload safe, as in count_populated.
                lines.append(f"{label}: dict({len(node)} keys, not descended)")
                return
            if _is_data_keyed(node):
                lines.append(f"{label}: dict({len(node)} data-shaped keys)")
                walk(next(iter(node.values())), f"{label}.<key>", depth + 1)
                return
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key), depth + 1)
            return

        if isinstance(node, (list, tuple)):
            lines.append(f"{label}: list({len(node)})")
            if not node or depth >= _MAX_SHAPE_DEPTH:
                return
            # First element only. Stress returned 1935 leaves, nearly all of it
            # one per-minute sample array; describing every element would make
            # the diagnostic exactly as unreadable as the payload it explains.
            walk(node[0], f"{label}[0]", depth + 1)
            return

        lines.append(f"{label}: {_type_name(node)}")

    walk(payload, "", 0)
    if truncated:
        lines.append(f"... capped at {max_lines} lines")
    return lines


@dataclass(frozen=True)
class Shape:
    key: str
    group: str
    verdict: str
    populated: int
    day: str
    lines: list[str]
    detail: str = ""


def explain(
    tokens_path: str,
    keys: list[str],
    day_offsets: tuple[int, ...] = DEFAULT_DAY_OFFSETS,
    on_progress=None,
) -> list[Shape]:
    """Describe the shape of what each named endpoint returns.

    Stops at the first date that yields a populated payload, so the common case
    costs one request rather than three. An endpoint that stays empty is still
    described from the last payload it returned -- that empty envelope is the
    interesting case, not a failure to report.
    """
    api = source._connect()
    today = datetime.now(UTC).date()
    days = [str(today - timedelta(days=n)) for n in day_offsets]
    by_key = {c.key: c for c in CANDIDATES}

    shapes: list[Shape] = []
    for key in keys:
        cand = by_key[key]
        best: tuple[Result, Any] | None = None

        for day in days:
            result, payload = _fetch(api, cand, day, tokens_path)
            if best is None or result.populated > best[0].populated:
                best = (result, payload)
            if result.verdict in (HAS_DATA, NOT_FOUND, ERROR):
                # Populated, or a verdict no further date will improve.
                break

        result, payload = best  # type: ignore[misc]
        lines = describe_shape(payload) if payload is not None else []
        shape = Shape(
            result.key, result.group, result.verdict,
            result.populated, result.day, lines, result.detail,
        )
        shapes.append(shape)
        if on_progress:
            on_progress(shape)

    return shapes
