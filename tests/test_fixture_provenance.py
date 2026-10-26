"""Every Garmin payload fixture names the session that recorded it (§8).

Both metric-tool test files already open by declaring this -- "payload shapes
here are the ones 5b actually recorded, not invented ones" -- and until now
nothing enforced it. A discipline with no test is a convention, and conventions
erode: `fetch.py`'s own docstring says so about the seam, and this is the same
shape of rule.

**Why this is the guard that would have caught 2026-10-26.** The claim that
`hrv_status` and `hrv_feedback` could point opposite ways was written into a map
note, a tool docstring, a test docstring, the journal and a PR body -- in the
indicative, from a single paraphrase of a phone screen. A claim about payload
behaviour belongs in a test, and a test needs a fixture: writing
`test_the_two_fields_can_disagree` would have meant inventing a payload with
disagreeing values and citing no session for it. This check fires exactly there,
at the moment the fabrication has to be written down.

What it cannot do is judge whether a cited session really said that. It makes
the citation mandatory and visible in review, which is the difference between a
claim somebody has to defend and a sentence nobody re-reads.
"""

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

#: The files whose fixtures stand in for real Garmin responses. Listed rather
#: than globbed: `test_compose.py`'s step lists are OUR inputs, invented on
#: purpose, and demanding a session for them would train everyone to write a
#: meaningless marker -- which is worse than no marker, because it looks like
#: evidence.
PAYLOAD_FILES = (
    "test_garmin_health.py",
    "test_garmin_training.py",
    "test_write.py",
    "test_garmin_workout_create.py",
)

#: Two labels, and the second is not a loophole.
#:
#: `# recorded:` cites the session that saw the shape. `# invented:` says that
#: nobody has -- which is the truth for `upload_workout` and `schedule_workout`,
#: never called by this project, so their response keys are unknown. A file full
#: of invented shapes needs to SAY so; forcing a citation it cannot give would
#: produce a fake one, and a fake citation is worse than none because it reads
#: as evidence.
#:
#: An `invented:` marker is therefore a standing invitation: the first live call
#: that sees the real shape should replace it with a `recorded:` one.
#:
#: `# recorded in part:` is the third state, added 2026-10-27 when the first real
#: write produced exactly it. The call returned both ids, so their value shapes
#: are known -- but the server attaches the raw response only on a miss, so WHICH
#: response key carried each id stayed unknown. "Partly recorded" was the honest
#: label and neither of the other two fit: `recorded:` would overclaim and
#: `invented:` would throw away what the live call did settle.
MARKERS = ("# recorded:", "# recorded in part:", "# invented:")

#: How far above an assignment the marker may sit. Enough for a comment block
#: that explains the shape, tight enough that it belongs to this fixture and not
#: to the one before it.
LOOKBACK = 8


def payload_fixtures(path: Path):
    """Module-level dict and list constants -- the shape a Garmin response takes.

    Walks `tree.body` rather than `ast.walk`, so a dict built inside a test
    function is not caught: a one-off shape written to exercise one case is
    argued for by the test around it, while a module-level constant is reused
    and quietly becomes the project's record of what Garmin returns.
    """
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, (ast.Dict, ast.List)):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            yield target.id, node.lineno


def marker_above(lines: list[str], lineno: int) -> str:
    """The marker line for an assignment at `lineno`, or an empty string."""
    start = max(0, lineno - 1 - LOOKBACK)
    for line in lines[start : lineno - 1]:
        stripped = line.strip()
        if any(stripped.startswith(marker) for marker in MARKERS):
            return stripped
    return ""


CASES = [
    (name, path.name, lineno, path)
    for path in (TESTS / f for f in PAYLOAD_FILES)
    for name, lineno in payload_fixtures(path)
]


def test_there_are_fixtures_to_check():
    """Guards the guard. A glob or a parse that quietly matched nothing would
    make every assertion below vacuously true -- the failure mode
    `test_layering.py` names for the same reason."""
    assert len(CASES) >= 6
    assert {case[1] for case in CASES} == set(PAYLOAD_FILES)


@pytest.mark.parametrize(
    "name,filename,lineno,path", CASES, ids=[f"{c[1]}::{c[0]}" for c in CASES]
)
def test_every_payload_fixture_cites_a_session(name, filename, lineno, path):
    marker = marker_above(path.read_text().splitlines(), lineno)

    assert marker, (
        f"{filename}:{lineno} {name} is a payload fixture with no "
        f"{' or '.join(MARKERS)} line within {LOOKBACK} lines above it. Say "
        "which session recorded it -- '5b, 2026-10-11', 'probe --explain "
        "sleep, 2026-10-25' -- or label it '# invented:' and say why no session "
        "has seen it. An unlabelled shape gets read as evidence for whatever "
        "the map or a docstring claims about it."
    )
    assert marker not in MARKERS, (
        f"{filename}:{lineno} {name} cites nothing after the colon"
    )


def test_the_check_can_actually_fail(tmp_path):
    """A provenance check that has never been seen to fire is decoration."""
    offender = tmp_path / "offender.py"
    offender.write_text('PAYLOAD = {"hrvSummary": {"status": "BALANCED"}}\n')

    names = list(payload_fixtures(offender))
    assert names == [("PAYLOAD", 1)]
    assert marker_above(offender.read_text().splitlines(), 1) == ""


def test_a_marker_on_the_wrong_fixture_is_not_borrowed(tmp_path):
    """The lookback must not let one fixture's citation cover the next one.

    Without a bound, a single marker at the top of a file would bless every
    shape below it, which is how a provenance rule becomes a decoration that
    passes.
    """
    offender = tmp_path / "offender.py"
    offender.write_text(
        "# recorded: 5b, 2026-10-11\n"
        "FIRST = {}\n"
        + "\n" * (LOOKBACK + 2)
        + "SECOND = {}\n"
    )
    lines = offender.read_text().splitlines()
    first, second = list(payload_fixtures(offender))

    assert marker_above(lines, first[1]), "the cited one passes"
    assert not marker_above(lines, second[1]), "the distant one does not borrow it"
