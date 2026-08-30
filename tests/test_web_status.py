"""The status page answers "is this machine set up", never "what is in there".

It is also the one page that must cost nothing: it exists to be reloaded while
you are fixing something, and a page that calls Garmin on every refresh would
rate limit the account it is trying to diagnose (docs/SCOPE.md §6).
"""

import os
import re

from starlette.testclient import TestClient

from garmin_mcp.web import app as web_app
from garmin_mcp.web.app import create_app, default_allowed_hosts, default_status_rows

GOOD_HOST = "127.0.0.1:8765"


def build(**kwargs):
    kwargs.setdefault("status_rows", lambda: [("Token file", "/data/x.json", "")])
    kwargs.setdefault("version", lambda: "0.2.0+abc1234")
    kwargs.setdefault("bootstrap", lambda e, p: "x")
    return TestClient(
        create_app(allowed_hosts=default_allowed_hosts(8765), **kwargs)
    )


class TestThePage:
    def test_it_reports_the_running_build(self):
        """`docker run` never re-pulls a moving tag, so "which build is this"
        is a real question with no other answer (§7)."""
        response = build().get("/", headers={"Host": GOOD_HOST})

        assert "0.2.0+abc1234" in response.text

    def test_it_renders_the_rows_it_is_given(self):
        client = build(status_rows=lambda: [("flock", "supported", "good")])

        response = client.get("/", headers={"Host": GOOD_HOST})

        assert "flock" in response.text
        assert "supported" in response.text

    def test_row_values_are_escaped(self):
        client = build(status_rows=lambda: [("Token file", "<img src=x>", "")])

        response = client.get("/", headers={"Host": GOOD_HOST})

        assert "<img src=x>" not in response.text

    def test_it_says_it_makes_no_garmin_call(self):
        """Stated on the page because the operator's next question after a
        429 is "did reloading this cause it"."""
        response = build().get("/", headers={"Host": GOOD_HOST})

        assert "no</strong> call to Garmin" in response.text


class TestTheRowsThemselves:
    def test_missing_tokens_are_reported_as_missing_not_as_an_error(self, tmp_path, monkeypatch):
        """A machine that has never been bootstrapped is the normal starting
        state, not a fault. This is exactly the state of a fresh laptop."""
        monkeypatch.setenv("GARMINTOKENS", str(tmp_path / "garmin_tokens.json"))

        rows = dict((label, value) for label, value, _ in default_status_rows())

        assert "none" in rows["Tokens"]

    def test_present_tokens_report_mode_and_write_time(self, tmp_path, monkeypatch):
        tokens = tmp_path / "garmin_tokens.json"
        tokens.write_text("{}")
        os.chmod(tokens, 0o600)
        monkeypatch.setenv("GARMINTOKENS", str(tokens))

        rows = {label: (value, css) for label, value, css in default_status_rows()}

        assert rows["Tokens"] == ("present", "good")
        assert rows["Permissions"] == ("0600", "good")
        assert re.match(r"\d{4}-\d\d-\d\dT", rows["Written (UTC)"][0])

    def test_loose_permissions_are_flagged_not_merely_shown(self, tmp_path, monkeypatch):
        """0600 is a §4 requirement. A status page that displays 0644 in the
        same colour as 0600 has reported nothing."""
        tokens = tmp_path / "garmin_tokens.json"
        tokens.write_text("{}")
        os.chmod(tokens, 0o644)
        monkeypatch.setenv("GARMINTOKENS", str(tokens))

        rows = {label: (value, css) for label, value, css in default_status_rows()}

        assert rows["Permissions"] == ("0644", "bad")

    def test_no_row_ever_contains_the_token_file_contents(self, tmp_path, monkeypatch):
        secret = "oauth2-token-value-that-must-never-be-rendered"
        tokens = tmp_path / "garmin_tokens.json"
        tokens.write_text('{"access_token": "%s"}' % secret)
        monkeypatch.setenv("GARMINTOKENS", str(tokens))

        rendered = " ".join(f"{a} {b}" for a, b, _ in default_status_rows())

        assert secret not in rendered

    def test_locking_is_probed_by_taking_a_real_lock(self, tmp_path, monkeypatch):
        """Not by checking that fcntl imports -- that only proves we are on a
        UNIX, and it is the volume that varies, not the platform."""
        monkeypatch.setenv("GARMINTOKENS", str(tmp_path / "garmin_tokens.json"))

        rows = {label: value for label, value, _ in default_status_rows()}

        assert rows["flock"] == "supported"
        assert (tmp_path / ".garmin.lock").exists()

    def test_the_budget_shown_is_the_one_actually_in_force(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GARMINTOKENS", str(tmp_path / "garmin_tokens.json"))
        monkeypatch.setenv("GARMIN_RATE_PER_SEC", "0.25")
        monkeypatch.setenv("GARMIN_RATE_BURST", "3")

        rows = {label: value for label, value, _ in default_status_rows()}

        assert "0.25 req/s" in rows["Request budget"]
        assert "burst 3" in rows["Request budget"]
