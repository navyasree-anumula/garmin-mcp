"""The laptop re-pin script, held to the rules that have broken it (v1.9).

**Why a PowerShell file has a Python test.** Three handovers of this script have
failed on how it was *delivered* rather than on what it does: a long
`Join-Path` line that did not survive a paste, prose between fenced blocks that
got saved into the `.ps1` and ran as a command, and -- on 2026-11-22 -- a
113-character `WriteAllText` line that wrapped and produced
`Missing closing ')' in expression`. That last one was the shape **this
project's own memory recommended**, so the advice carried the bug.

The script is in the repository now exactly so a handover is a download line
rather than a paste. These assertions are the half that keeps it that way: a
rule nothing holds is a comment, and this one has cost three evenings.

**What this file deliberately does NOT do.** It does not run PowerShell -- that
needs Windows and a Desktop config -- so it asserts the file's *properties*, not
its behaviour. The behavioural guard is the script's own: the digest assertion
and the capability probe, both of which run before anything is written.
"""

from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "repin.ps1"

#: Narrow enough that no terminal or chat column wraps it. The failure this
#: guards is not cosmetic: a wrap inside *code* is a parse error you see at
#: once, while a wrap inside a **double-quoted string** -- a path default, a
#: message -- parses fine and silently changes the value.
MAX_WIDTH = 72


def lines():
    return SCRIPT.read_text(encoding="utf-8").splitlines()


def code_lines():
    """The lines that execute. A comment naming a forbidden cmdlet in order to
    explain why it is forbidden must not trip the guard against using it --
    that is a guard reading its own documentation as a violation."""
    return [line for line in lines() if not line.strip().startswith("#")]


def too_wide(text_lines, limit=MAX_WIDTH):
    """Every (number, width) that exceeds the limit. Shared with the control."""
    return [
        (number, len(line))
        for number, line in enumerate(text_lines, start=1)
        if len(line) > limit
    ]


def test_the_script_is_in_the_repository():
    """The point of committing it: the handover stops being a paste."""
    assert SCRIPT.is_file()
    assert lines(), "the script is empty"


def test_no_line_is_long_enough_to_wrap():
    offenders = too_wide(lines())

    assert offenders == [], (
        f"{SCRIPT.name} has lines over {MAX_WIDTH} characters: {offenders}. A "
        "line that wraps on its way into a .ps1 breaks the file, and a wrap "
        "inside a double-quoted string breaks it silently. Build long values "
        "from short pieces -- `Join-Path` steps, or `+=` on a string."
    )


def test_the_width_check_can_actually_fail(tmp_path):
    """The negative control every guard in this repo carries."""
    planted = ["$short = 1", "$long = '" + "x" * MAX_WIDTH + "'"]

    assert too_wide(planted) == [(2, MAX_WIDTH + 10)]


def test_the_digest_argument_is_mandatory():
    """A re-pin that only pulls a moving tag is how this repository's docs
    asserted a wrong pin for five days."""
    text = SCRIPT.read_text(encoding="utf-8")
    digest_line = text.index("$Digest,")
    mandatory = text.rindex("Mandatory = $true", 0, digest_line)

    assert digest_line - mandatory < 120, (
        "$Digest must carry [Parameter(Mandatory = $true)] immediately above it"
    )


def test_the_digest_is_asserted_before_anything_is_written():
    """The order is the design: the step most likely to fail runs before
    anything that leaves a trace."""
    text = SCRIPT.read_text(encoding="utf-8")

    assert text.index("Contains($Digest)") < text.index("WriteAllText")
    assert text.index("Copy-Item") < text.index("WriteAllText")


def test_the_bom_writing_cmdlet_is_never_used():
    """`Set-Content -Encoding UTF8` writes a BOM on PowerShell 5.1, and the
    Desktop config has never had one -- so that flag rewrites a working config
    into one a strict parser can reject."""
    code = "\n".join(code_lines())

    assert "Set-Content" not in code, (
        "the cmdlet may be named in a comment explaining why it is not used, "
        "and must not be called"
    )
    assert "UTF8Encoding $false" in code
    assert "0xFEFF" in code, "the no-BOM claim must be asserted after writing"


def test_the_probe_payload_carries_no_quote_characters():
    """A quote inside a native command's argument does not survive. The payload
    is built from single-quoted pieces with nothing quoted inside them, and
    `chr(10)` stands in for a newline for exactly that reason."""
    payload = [
        line
        for line in lines()
        if line.strip().startswith("$probe") and "-replace" not in line
    ]

    assert len(payload) >= 4, "the payload should be built from short pieces"
    for line in payload:
        assert '"' not in line, f"double quote in payload line: {line}"
        assert line.count("'") == 2, f"nested quote in payload line: {line}"


def test_the_build_is_verified_by_capability_and_not_by_a_version_string():
    """The rule this repo wrote after `server_version` reached a client and was
    summarised away. The instruments are the digest, the probe and `docker ps`."""
    text = SCRIPT.read_text(encoding="utf-8")

    assert "server_version" not in text
    assert "list_tools" in text
    assert "docker ps" in text


def test_the_writes_flag_is_asserted_to_survive_the_edit():
    """`docker run` does not forward the parent environment, so writes are
    enabled by `-e GARMIN_MCP_ENABLE_WRITES=1` in the config's `args`. A re-pin
    that dropped it would look configured and leave writes off."""
    text = SCRIPT.read_text(encoding="utf-8")

    assert "GARMIN_MCP_ENABLE_WRITES" in text
    assert text.index("GARMIN_MCP_ENABLE_WRITES") < text.index("WriteAllText")


def test_the_config_path_is_searched_for_and_not_assumed():
    """The MSIX path was hardcoded until 2026-11-22, when it stopped existing on
    the laptop and step 3 refused -- correctly, but a guess that has gone stale
    once should become a search. Both install shapes are covered: the standard
    installer under `%APPDATA%\\Claude`, and ANY `Packages\\Claude*` rather than
    one package id, because that id is exactly what changed."""
    code = "\n".join(code_lines())

    assert "$env:APPDATA" in code, "the standard installer's location"
    assert "'Claude*'" in code, "any MSIX package, not one id"
    assert "-ConfigPath" in code, "and an override when neither is right"


def test_an_ambiguous_config_location_is_refused_not_picked():
    """Two configs is a state where choosing one is an invisible arbitrary
    choice -- the same argument `resolve` makes for two templates of one name.
    A bounded refusal beats a silent partial."""
    code = "\n".join(code_lines())
    ambiguous = code.index("$found.Count -gt 1")

    assert "ambiguous" in code[ambiguous:ambiguous + 400]
    assert code.index("throw", ambiguous) < code.index("WriteAllText")


@pytest.mark.parametrize("fenced", ("```", "~~~"))
def test_no_markdown_fence_was_saved_into_the_script(fenced):
    """One earlier handover failed because prose between fenced blocks was saved
    into the `.ps1` and ran as a command. A fence in the file means the same
    thing happened again."""
    assert fenced not in SCRIPT.read_text(encoding="utf-8")
