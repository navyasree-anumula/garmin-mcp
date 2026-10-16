"""The map is a tested artifact, not a lookup table somebody trusts (§5, v1.4).

Four of nine known metrics come from somewhere other than the endpoint named
after them, which is why these assertions exist at all. Two of them are cheap in
a way worth copying: checking that every row names a method the library actually
has catches a typo without spending a Garmin request, and checking that every
unverified row carries a note keeps the map honest about which field paths were
read off a real payload and which were inferred.

§13.9's finding 8.a is the shape of the hard half: "every metric resolves to a
real library method" cannot be asserted, because `spo2` resolves to nothing on
purpose. A test that called that a failure would be wrong about the design.
"""

import garminconnect
import pytest

from garmin_mcp.source import metrics
from garmin_mcp.source.extract import DECODERS


def test_there_are_rows_to_check():
    """Guards the assertions below from passing on an empty tuple."""
    assert len(metrics.METRICS) >= 10


@pytest.mark.parametrize("metric", metrics.METRICS, ids=lambda m: m.key)
def test_every_source_method_exists_on_the_client(metric):
    """A typo in a method name costs a request to discover, or worse, an
    `error` verdict that looks like Garmin's fault. This finds it for free --
    the same trick `test_every_candidate_method_exists_on_the_client` plays for
    the probe's candidates."""
    if metric.source_method is None:
        pytest.skip("no-source row, covered by its own test")
    assert hasattr(garminconnect.Garmin, metric.source_method)


def test_the_no_source_row_is_asserted_as_having_none():
    """The second half of 8.a. SpO2 is measured-unsupported on this watch, so
    the map records an absence rather than omitting the metric -- which is what
    keeps "this watch does not measure SpO2" an answer the agent can give."""
    assert metrics.BY_KEY["spo2"].source_method is None
    assert metrics.BY_KEY["spo2"].note, "an absence has to say why"


@pytest.mark.parametrize("metric", metrics.METRICS, ids=lambda m: m.key)
def test_numbers_have_units_and_phrases_do_not(metric):
    """§5: never emit a bare number. A phrase is Garmin's own wording for a
    code with no sourced meaning, and giving it a unit would be a lie."""
    if metric.source_method is None:
        pytest.skip("no reading, so no unit to carry")
    if metric.kind == metrics.KIND_NUMBER:
        assert metric.unit, f"{metric.key} is a number with no unit"
    else:
        assert metric.unit == "", f"{metric.key} is a phrase carrying a unit"


@pytest.mark.parametrize("metric", metrics.METRICS, ids=lambda m: m.key)
def test_an_unverified_path_says_what_would_confirm_it(metric):
    """Half the rows were inferred rather than recorded. That is acceptable;
    being quiet about which is not."""
    if not metric.verified:
        assert metric.note, f"{metric.key} is unverified and silent about it"


@pytest.mark.parametrize("metric", metrics.METRICS, ids=lambda m: m.key)
def test_decoder_and_call_names_are_known(metric):
    assert metric.decoder == "" or metric.decoder in DECODERS
    assert metric.call in {metrics.CALL_DATE, metrics.CALL_NONE}


def test_keys_are_unique():
    assert len(metrics.KEYS) == len(set(metrics.KEYS))


def test_three_metrics_from_one_payload_cost_one_request():
    """The reason per-call coalescing exists, and the reason v1.5 could ship the
    seam without the store: this duplication is inside a single call."""
    calls = metrics.sources_for(["training_status", "load_balance", "vo2_max"])
    assert calls == (metrics.SourceCall("get_training_status", metrics.CALL_DATE),)


def test_a_repeated_metric_does_not_repeat_its_source():
    calls = metrics.sources_for(["vo2_max", "vo2_max"])
    assert len(calls) == 1


def test_the_whole_training_group_is_three_requests():
    """Nine metrics, three endpoints. Worth asserting as a number: it is the
    figure §6's budget arithmetic in the read sweep is built on."""
    calls = metrics.sources_for(metrics.keys_for_group("training"))
    assert len(calls) == 3
    assert {c.method for c in calls} == {
        "get_training_status",
        "get_training_readiness",
        "get_race_predictions",
    }


def test_a_no_source_metric_adds_no_request():
    assert metrics.sources_for(["spo2"]) == ()


def test_race_predictions_is_called_with_no_arguments():
    """`get_race_predictions` accepts nought arguments or three and raises
    ValueError for anything between -- the call shape that made the probe
    record an ERROR for a month that was entirely our own."""
    row = metrics.BY_KEY["race_5k"]
    assert row.call == metrics.CALL_NONE


def test_unknown_key_raises_rather_than_resolving_to_nothing():
    with pytest.raises(KeyError):
        metrics.resolve(["vo2_maxx"])


def test_the_training_group_excludes_health_metrics():
    assert "spo2" not in metrics.keys_for_group("training")


def test_lactate_threshold_is_absent_until_it_is_measured():
    """Deliberate, and asserted so it cannot be added by inference. It is not
    among the probe's candidates, so a row for it would be invention -- and the
    map's whole claim is that a field path is a measurement."""
    assert "lactate_threshold" not in metrics.BY_KEY
