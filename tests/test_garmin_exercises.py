"""`garmin_exercises` -- a lookup that reaches nothing (§5).

This tool exists because refusing was not enough. A strength step must name one
entry from Garmin's catalogue, and `resolve_exercise` refuses an ambiguous name
rather than picking the nearest -- which is right, because a guess puts a
different exercise on the watch under a name that reads correct. But the first
live strength session, on 2026-10-31, then had no way to resolve that ambiguity
except to guess again, and spent three turns on it.

The whole file runs with the Garmin doorway booby-trapped, because "reaches
nothing" is the tool's central claim: the catalogue is 1527 entries compiled
into the library, not an endpoint. A doorway that raises proves there is no path
to a request at all, which counting calls cannot.

The load-bearing test here is `test_every_name_a_search_returns_resolves`. The
tool is only worth having if its output can be handed straight back to a step,
and that is a property of two functions agreeing rather than of either alone.
"""

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp.server import garmin_exercises, mcp
from garmin_mcp.source import client as source
from garmin_mcp.source import compose


@pytest.fixture(autouse=True)
def no_garmin_doorway(monkeypatch):
    """Booby-trap every route to Garmin for the whole file."""

    def explode(*args, **kwargs):
        raise AssertionError("a catalogue lookup must not open a Garmin request")

    monkeypatch.setattr(source, "_garmin_access", explode)
    monkeypatch.setattr(source, "_connect", explode)


def registered_tool(name: str):
    by_name = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    assert name in by_name, f"{name} is not registered: {sorted(by_name)}"
    return by_name[name]


# ------------------------------- the search -------------------------------


def test_a_search_finds_the_obvious_thing():
    result = garmin_exercises(search="goblet squat")

    assert result.requests_made == 0
    assert result.matches[0].name == "Goblet Squat"
    assert result.matches[0].category == "SQUAT"
    assert result.matches[0].exercise == "GOBLET_SQUAT"


def test_all_words_must_appear_but_the_order_does_not_matter():
    """Looking should be easy and committing should be strict. `resolve_exercise`
    needs the words in order; a search does not."""
    forwards = garmin_exercises(search="goblet squat")
    backwards = garmin_exercises(search="squat goblet")

    assert [m.name for m in forwards.matches] == [m.name for m in backwards.matches]


def test_an_exact_name_ranks_first():
    """'Squat' is a display name and a substring of 125 others. If the exact
    entry were buried the search would be useless for the commonest case."""
    result = garmin_exercises(search="squat")

    assert result.matches[0].name == "Squat"


def test_names_starting_with_the_term_rank_above_names_merely_containing_it():
    result = garmin_exercises(search="reverse lunge", limit=25)
    names = [m.name for m in result.matches]

    first_starts = names.index("Reverse Lunge with Reach Back")
    contains = names.index("Dumbbell Reverse Lunge")

    assert first_starts < contains


def test_the_order_is_stable_across_calls():
    """Alphabetical within each band, so the ranking does not depend on the
    catalogue's own ordering and the same question reads the same twice."""
    first = [m.name for m in garmin_exercises(search="press", limit=20).matches]
    second = [m.name for m in garmin_exercises(search="press", limit=20).matches]

    assert first == second


def test_case_does_not_matter():
    assert (
        garmin_exercises(search="GOBLET SQUAT").matches[0].name
        == garmin_exercises(search="goblet squat").matches[0].name
    )


# ----------------------------- the truncation -----------------------------


def test_a_capped_list_says_it_was_capped():
    """A truncated list that does not say so reads as "these are the only
    ones", which is how a human concludes an exercise does not exist."""
    result = garmin_exercises(search="press", limit=5)

    assert result.shown == 5
    assert result.total_matches > 5
    assert result.truncated is True


def test_an_uncapped_list_says_it_was_not():
    result = garmin_exercises(search="goblet squat", limit=25)

    assert result.shown == result.total_matches
    assert result.truncated is False


def test_raising_the_limit_returns_more():
    small = garmin_exercises(search="press", limit=5)
    large = garmin_exercises(search="press", limit=40)

    assert large.shown > small.shown
    assert large.total_matches == small.total_matches


# ------------------------- the property that matters ----------------------


@pytest.mark.parametrize(
    "term", ["squat", "lunge", "press", "plank", "row", "deadlift", "raise", "curl"]
)
def test_every_name_a_search_returns_resolves(term):
    """**The reason this tool is worth having.** Its output is only useful if a
    name can be handed straight back to a step, and that is two functions
    agreeing rather than either one being correct. If `resolve_exercise` ever
    grew stricter, or the search started returning something other than
    catalogue display names, this is what would catch it.
    """
    result = garmin_exercises(search=term, limit=40)

    for match in result.matches:
        resolved = compose.resolve_exercise(match.name)
        assert resolved["category"] == match.category
        assert resolved["exercise"] == match.exercise


# ------------------------------ the refusals ------------------------------


@pytest.mark.parametrize("term", ["", "   "])
def test_an_empty_search_is_refused(term):
    with pytest.raises(ToolError, match="needs something to search for"):
        garmin_exercises(search=term)


@pytest.mark.parametrize("limit", [0, -1])
def test_a_limit_below_one_is_refused(limit):
    with pytest.raises(ToolError, match="limit must be at least 1"):
        garmin_exercises(search="squat", limit=limit)


def test_no_match_says_how_big_the_catalogue_is():
    """A bare "no results" invites the agent to conclude the exercise does not
    exist. Naming the catalogue size and offering broader words makes the next
    move obvious."""
    with pytest.raises(ToolError) as exc:
        garmin_exercises(search="nonsense exercise xyz")

    message = str(exc.value)
    assert "1527" in message
    assert "squat" in message


# ------------------------------ the surface -------------------------------


def test_the_annotations_say_it_touches_nothing():
    """`open_world_hint=False` is the honest distinction from every other tool
    here: this one has no external system behind it at all."""
    annotations = registered_tool("garmin_exercises").annotations

    assert annotations.read_only_hint is True
    assert annotations.destructive_hint is False
    assert annotations.idempotent_hint is True
    assert annotations.open_world_hint is False


def test_the_default_limit_comes_from_the_composer():
    """Derived, not restated -- the same rule as the step kinds and the sports."""
    schema = registered_tool("garmin_exercises").input_schema

    assert schema["properties"]["limit"]["default"] == compose.DEFAULT_SEARCH_LIMIT


def test_the_other_tools_are_undisturbed():
    """A sixth tool must not perturb the five that work."""
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert {
        "garmin_auth_status",
        "garmin_training",
        "garmin_health",
        "garmin_exercises",
        "garmin_workouts",
        "garmin_workout_create",
    } <= names


def test_the_note_says_it_reached_nothing():
    """The note is the only place a summarising client might carry the fact
    that this call is free, which is what makes it safe to run repeatedly."""
    note = garmin_exercises(search="squat").note

    assert "Nothing was sent to Garmin" in note
    assert "0" in note or "no" in note.lower()
