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
def test_every_row_states_what_evidence_put_it_here(metric):
    """`provenance` answers a different question from `verified`.

    `verified` is about the dotted path: was it read off a real payload?
    `provenance` is about the row: what evidence put it in the map at all? The
    combination that matters is a **verified path with a reported reason**,
    which is what happened on 2026-10-26 -- `hrv_feedback`'s path was genuinely
    recorded by 5b, the reason for adding it was one paraphrase of a phone
    screen, and a mechanism nobody had measured went into five places in the
    indicative. Nothing in the map could express that state, so nothing caught
    it.

    The field defaults to `inferred`, the most pessimistic value, so a row added
    without stating its evidence trips the count assertion below rather than
    quietly claiming a measurement.
    """
    assert metric.provenance in metrics.PROVENANCE


def test_the_number_of_rows_not_backed_by_a_payload():
    """**The teeth.** A literal, so adding such a row means editing a test.

    Today every row is in the map because a payload this project received showed
    the field -- including the four whose `verified` is False, where the payload
    was read and it is *which* field answers that was inferred. That is the
    orthogonality working: same evidence for the row, different confidence in
    the path.

    So the number is zero, and it is asserted rather than described. A row on
    somebody's word is a legitimate thing to add -- `hrv_feedback` was one for
    about an hour -- but it should cost a deliberate edit to a number here,
    the way `test_the_whole_health_group_is_four_requests` makes a request-count
    change deliberate. If this assertion fails, the right fix is usually to
    change the number and say why in the row's note, not to relabel the row.
    """
    not_measured = [
        m.key for m in metrics.METRICS if m.provenance != metrics.PROV_MEASURED
    ]

    assert not_measured == [], (
        f"{not_measured} are in the map on something other than a payload. "
        "That is allowed; it is not allowed to be silent. Update this count "
        "and make the row's note say what the evidence actually was."
    )


def test_a_row_not_backed_by_a_payload_says_so_in_its_note():
    """A reported or inferred row has to carry the words, because the note is
    what reaches the reading -- `readings.read` attaches it for any row that is
    not both verified and measured.

    Deliberately not parametrised. Every row is measured today, so a
    parametrised version would skip twenty times and bury the suite's skip
    count, which is a number this repo reads as a signal. Vacuous today, and it
    has teeth the moment the count above changes.
    """
    for metric in metrics.METRICS:
        if metric.provenance == metrics.PROV_MEASURED:
            continue
        assert metric.note, f"{metric.key} is {metric.provenance} and silent about it"
        assert metric.provenance in metric.note.lower(), (
            f"{metric.key} is {metric.provenance} but its note does not say the "
            "word, and the note is what the agent reads"
        )


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
