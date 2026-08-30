"""The default bootstrap path — the one that actually runs on the laptop.

Every other web test injects a fake `bootstrap`, which is right for testing the
form and wrong for testing the wiring underneath it. This file exercises
`_default_bootstrap` for real: through `source.client.bootstrap_login`, through
`_garmin_access`, through `_translate`. Only `garminconnect.Garmin` itself is
replaced, because that is the one thing that would reach the network.

It exists because the MFA branch was dead code and nothing above this level
could have noticed.
"""

import os
from pathlib import Path

import pytest

from garmin_mcp.source import client as source
from garmin_mcp.source.errors import GarminSourceError, RateLimited
from garmin_mcp.web.app import MfaRequired, _default_bootstrap


class FakeGarmin:
    """Stands in for the library. `behaviour` runs inside `login()`, which is
    where garminconnect invokes `prompt_mfa` and where every failure surfaces."""

    def __init__(self, behaviour=None):
        self.behaviour = behaviour

    def __call__(self, email=None, password=None, prompt_mfa=None):
        self.email, self.password, self.prompt_mfa = email, password, prompt_mfa
        self.display_name = "a-guid-not-a-name"
        return self

    def login(self, tokenstore=None):
        if self.behaviour:
            self.behaviour(self)
        Path(tokenstore).write_text('{"access_token": "x"}')


@pytest.fixture
def tokens(tmp_path, monkeypatch):
    path = tmp_path / "garmin_tokens.json"
    monkeypatch.setenv("GARMINTOKENS", str(path))
    return path


def install(monkeypatch, behaviour=None) -> FakeGarmin:
    fake = FakeGarmin(behaviour)
    monkeypatch.setattr(source, "Garmin", fake)
    return fake


class TestTheHappyPath:
    def test_credentials_reach_the_library_and_tokens_are_written(self, tokens, monkeypatch):
        fake = install(monkeypatch)

        who = _default_bootstrap("mahi@example.com", "hunter2")

        assert who == "a-guid-not-a-name"
        assert (fake.email, fake.password) == ("mahi@example.com", "hunter2")
        assert tokens.is_file()

    def test_tokens_are_written_owner_only(self, tokens, monkeypatch):
        """§4: the token file is the credential once the password is gone."""
        install(monkeypatch)

        _default_bootstrap("mahi@example.com", "hunter2")

        assert oct(tokens.stat().st_mode & 0o777) == "0o600"


class TestMfa:
    """The branch this file was written for."""

    def test_a_code_request_surfaces_as_MfaRequired(self, tokens, monkeypatch):
        """Raising from `prompt_mfa` does not reach us as itself: it crosses
        garminconnect's strategy loop and `bootstrap_login`'s broad
        `except Exception`, which re-types it via `_translate`. Detected by a
        flag set inside the callback instead."""
        install(monkeypatch, behaviour=lambda api: api.prompt_mfa())

        with pytest.raises(MfaRequired):
            _default_bootstrap("mahi@example.com", "hunter2")

    def test_the_form_renders_the_cli_fallback_for_it(self, tokens, monkeypatch):
        """End to end, through the real bootstrap: the page must name the route
        that exists, because the web flow cannot collect a code."""
        from starlette.testclient import TestClient

        from garmin_mcp.web.app import create_app, default_allowed_hosts

        install(monkeypatch, behaviour=lambda api: api.prompt_mfa())
        client = TestClient(create_app(allowed_hosts=default_allowed_hosts(8765)))

        page = client.get("/login", headers={"Host": "127.0.0.1:8765"})
        marker = 'name="csrf_token" value="'
        start = page.text.index(marker) + len(marker)
        token = page.text[start : page.text.index('"', start)]

        response = client.post(
            "/login",
            headers={"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"},
            data={"csrf_token": token, "email": "a@b.c", "password": "pw"},
        )

        assert "MFA code" in response.text
        assert "docker run -it" in response.text
        assert "<button type=submit disabled>" in response.text

    def test_an_ordinary_failure_is_not_mistaken_for_mfa(self, tokens, monkeypatch):
        """The mirror image. A flag that were set unconditionally would turn
        every failure into MFA advice, and this test would still pass without
        it."""
        def fails(api):
            raise ValueError("something else entirely")

        install(monkeypatch, behaviour=fails)

        with pytest.raises(GarminSourceError) as caught:
            _default_bootstrap("mahi@example.com", "hunter2")

        assert not isinstance(caught.value, MfaRequired)


class TestFailuresKeepTheirType:
    def test_a_login_429_still_arrives_as_RateLimited(self, tokens, monkeypatch):
        """The type the form keys on to disable the submit button. It must
        survive the extra layer this module adds."""
        from garminconnect import GarminConnectTooManyRequestsError

        def throttled(api):
            raise GarminConnectTooManyRequestsError("429")

        install(monkeypatch, behaviour=throttled)

        with pytest.raises(RateLimited):
            _default_bootstrap("mahi@example.com", "hunter2")

    def test_no_token_file_is_left_behind_when_login_fails(self, tokens, monkeypatch):
        """A partial or empty token file would make the next run report
        "tokens present" for something that cannot authenticate."""
        def fails(api):
            raise ValueError("nope")

        install(monkeypatch, behaviour=fails)

        with pytest.raises(GarminSourceError):
            _default_bootstrap("mahi@example.com", "hunter2")

        assert not tokens.exists()
