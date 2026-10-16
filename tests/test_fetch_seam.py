"""The seam: one request per distinct source, and one doorway to Garmin (§6, v1.5).

Two things are being pinned here. The first is coalescing -- three metrics out
of one payload must cost one request, because that duplication is what v1.4
wanted a cache for and it turns out to live inside a single call. The second is
the doorway: `fetch.py` is the only module on the data path that may call
`client._garmin_access`, and a call-site discipline with no test is a convention
that erodes the first time somebody is in a hurry.
"""

import ast
from pathlib import Path

import pytest
from garminconnect import GarminConnectNotFoundError

from garmin_mcp.source import metrics, verdicts
from garmin_mcp.source.errors import RateLimited
from garmin_mcp.source.fetch import fetch


class FakeApi:
    """Records what was asked of it. No payload shapes here -- decoding is
    `test_extract.py`'s job and this file is about requests."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.raises: dict[str, Exception] = {}
        self.payloads: dict[str, object] = {}

    def _record(self, name, *args):
        self.calls.append((name, args))
        if name in self.raises:
            raise self.raises[name]
        return self.payloads.get(name, {"ok": name})

    def get_training_status(self, date):
        return self._record("get_training_status", date)

    def get_training_readiness(self, date):
        return self._record("get_training_readiness", date)

    def get_race_predictions(self):
        return self._record("get_race_predictions")


@pytest.fixture
def api():
    return FakeApi()


@pytest.fixture
def tokens(tmp_path):
    path = tmp_path / "garmin_tokens.json"
    path.write_text("{}")
    return str(path)


def test_three_metrics_sharing_a_source_make_one_request(api, tokens):
    calls = metrics.sources_for(["training_status", "load_balance", "vo2_max"])
    fetch(api, calls, tokens, "2026-10-15")
    assert len(api.calls) == 1


def test_a_repeated_metric_makes_one_request(api, tokens):
    calls = metrics.sources_for(["vo2_max", "vo2_max", "vo2_max"])
    fetch(api, calls, tokens, "2026-10-15")
    assert len(api.calls) == 1


def test_the_whole_training_group_makes_three(api, tokens):
    calls = metrics.sources_for(metrics.keys_for_group("training"))
    fetch(api, calls, tokens, "2026-10-15")
    assert len(api.calls) == 3


def test_a_date_source_gets_the_date_and_a_none_source_gets_nothing(api, tokens):
    calls = metrics.sources_for(["vo2_max", "race_5k"])
    fetch(api, calls, tokens, "2026-10-15")
    by_name = dict(api.calls)
    assert by_name["get_training_status"] == ("2026-10-15",)
    assert by_name["get_race_predictions"] == ()


def test_a_404_is_unsupported_and_does_not_stop_the_others(api, tokens):
    """A dead endpoint must not take down the metrics that came from live ones:
    §5's only reliable "cannot" signal is a 404, and it is per source."""
    api.raises["get_race_predictions"] = GarminConnectNotFoundError("404")
    calls = metrics.sources_for(["vo2_max", "race_5k"])

    results = fetch(api, calls, tokens, "2026-10-15")

    predictions = results[metrics.SourceCall("get_race_predictions", metrics.CALL_NONE)]
    status = results[metrics.SourceCall("get_training_status", metrics.CALL_DATE)]
    assert predictions.verdict == verdicts.UNSUPPORTED
    assert status.verdict == verdicts.PRESENT


def test_a_rate_limit_is_re_raised_not_recorded(api, tokens):
    """§6 forbids retrying into a throttle, and a swallowed 429 would reach the
    agent as "no data" -- the account blamed for Garmin's rate limit."""
    api.raises["get_training_status"] = RateLimited()
    calls = metrics.sources_for(["vo2_max"])

    with pytest.raises(RateLimited):
        fetch(api, calls, tokens, "2026-10-15")


def test_any_other_failure_is_recorded_per_source(api, tokens):
    api.raises["get_training_status"] = TimeoutError("slow")
    calls = metrics.sources_for(["vo2_max"])

    results = fetch(api, calls, tokens, "2026-10-15")

    result = results[metrics.SourceCall("get_training_status", metrics.CALL_DATE)]
    assert result.verdict == verdicts.ERROR
    assert result.detail == "TimeoutError"


def test_an_unknown_call_shape_raises_because_it_is_our_bug(api, tokens):
    bogus = (metrics.SourceCall("get_training_status", "fortnightly"),)
    with pytest.raises(ValueError, match="unknown call shape"):
        fetch(api, bogus, tokens, "2026-10-15")


# ---------------------------------------------------------------------------
# The doorway rule
# ---------------------------------------------------------------------------

PACKAGE = Path(__file__).resolve().parent.parent / "garmin_mcp"

#: Four files may name the doorway, each for a stated reason:
#:
#: - `source/client.py` defines it.
#: - `source/fetch.py` is the seam -- the only data-path caller.
#: - `source/probe.py` probes one candidate at a time and has no duplication to
#:   coalesce, so routing it through the seam would add indirection and nothing
#:   else. Named rather than refactored; 41 tests ride on it.
#: - `cli.py` is the `selftest`, whose whole purpose is to exercise the lock and
#:   the budget on a real filesystem. It makes **no Garmin request at all**
#:   (`test_selftest_makes_no_garmin_calls`), so the rule it must not break --
#:   data reads coalesce -- does not apply to it.
ALLOWED = {
    "source/client.py",
    "source/fetch.py",
    "source/probe.py",
    "cli.py",
}


def _mentions_garmin_access(path: Path) -> bool:
    """Walks attributes, bare names AND import aliases.

    The aliases are not thoroughness for its own sake: the first version of
    this check walked only Attribute and Name, and the negative control below
    caught it passing a file whose entire content was
    `from .client import _garmin_access`. An `ast.alias` is neither.
    """
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Attribute) and node.attr == "_garmin_access":
            return True
        if isinstance(node, ast.Name) and node.id == "_garmin_access":
            return True
        if isinstance(node, ast.alias) and node.name == "_garmin_access":
            return True
    return False


def test_only_the_seam_and_its_exceptions_reach_the_doorway():
    offenders = sorted(
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if _mentions_garmin_access(path)
        and str(path.relative_to(PACKAGE)) not in ALLOWED
    )
    assert offenders == [], (
        f"{offenders} call client._garmin_access directly. Data reads go "
        "through source/fetch.py so they coalesce and so the cache store has "
        "one place to land (docs/SCOPE.md §6)."
    )


def test_the_doorway_check_can_actually_fail(tmp_path):
    """The negative control every guard in this repo carries. Without it, this
    file would pass just as happily if the AST walk were looking for the wrong
    name."""
    planted = tmp_path / "sneaky.py"
    planted.write_text("from .client import _garmin_access\n")
    assert _mentions_garmin_access(planted)
