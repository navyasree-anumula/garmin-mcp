"""A wrong password and an expired token arrive as the same library exception.

`GarminConnectAuthenticationError` is raised on both of our call paths and means
opposite things on each. Nothing on the exception distinguishes them, so the
only thing that can is which call was in flight -- and getting that wrong is not
cosmetic. The web form answered a mistyped password with

    Garmin rejected the stored tokens at /data/garmin_tokens.json.
    They have expired or been revoked. Re-run the bootstrap to get new ones.

on a machine with no token file, to somebody who was running the bootstrap. Every
noun in it was wrong.

The mirror mistake is just as bad: treating an expired token as a typo would tell
somebody to check a password when the fix is to re-authenticate.
"""

from pathlib import Path

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp.source import client as source
from garmin_mcp.source.errors import (
    AuthExpired,
    InvalidCredentials,
    NotBootstrapped,
    RateLimited,
)

REJECTED = GarminConnectAuthenticationError("401 Unauthorized (Invalid Username or Password)")


class TestTranslationDependsOnTheCallInFlight:
    def test_during_login_it_is_the_credentials(self):
        result = source._translate(REJECTED, "/data/garmin_tokens.json", during_login=True)

        assert isinstance(result, InvalidCredentials)

    def test_loading_tokens_it_is_the_tokens(self):
        result = source._translate(REJECTED, "/data/garmin_tokens.json")

        assert isinstance(result, AuthExpired)

    def test_the_default_is_the_token_path(self):
        """`during_login` must be opt-in: `_connect` is the hot path and calls
        it without the flag, so a default of True would mislabel every expiry."""
        assert not isinstance(
            source._translate(REJECTED, "/x"), InvalidCredentials
        )

    @pytest.mark.parametrize("during_login", [True, False])
    def test_a_429_is_a_429_either_way(self, during_login):
        """The new flag must not disturb the type the UI keys on to disable the
        submit button."""
        result = source._translate(
            GarminConnectTooManyRequestsError("429"), "/x", during_login=during_login
        )

        assert isinstance(result, RateLimited)


class TestTheMessages:
    def test_it_does_not_send_the_operator_after_the_token_file(self):
        """The bug in one line: the old message named a file the operator did
        not have, and prescribed the command they were already running.

        Not a blanket ban on the word "token" -- saying the tokens are untouched
        is reassurance, and the thing that hurt was the misdirection: a
        filename, a claim of expiry, and an instruction to re-bootstrap."""
        message = str(InvalidCredentials("401 Unauthorized")).lower()

        assert "garmin_tokens.json" not in message
        assert "expired" not in message
        assert "revoked" not in message
        assert "bootstrap" not in message

    def test_it_says_this_is_not_a_lockout(self):
        message = str(InvalidCredentials())

        assert "not a lockout" in message
        assert "connect.garmin.com" in message

    def test_it_keeps_the_librarys_own_words(self):
        """Previously discarded -- and it was the one line that said what had
        actually gone wrong."""
        message = str(InvalidCredentials("401 Unauthorized (Invalid Username or Password)"))

        assert "Invalid Username or Password" in message

    def test_an_expired_token_still_names_the_file_and_the_backup(self):
        message = str(AuthExpired("/data/garmin_tokens.json"))

        assert "/data/garmin_tokens.json" in message
        assert "Back up" in message


class TestThroughTheRealBootstrap:
    """Not just `_translate` in isolation: the flag has to survive being
    threaded through `bootstrap_login`."""

    @pytest.fixture
    def tokens(self, tmp_path, monkeypatch):
        path = tmp_path / "garmin_tokens.json"
        monkeypatch.setenv("GARMINTOKENS", str(path))
        return path

    def install(self, monkeypatch, error):
        class Fake:
            def __call__(self, email=None, password=None, prompt_mfa=None):
                return self

            def login(self, tokenstore=None):
                raise error

        monkeypatch.setattr(source, "Garmin", Fake())

    def test_a_rejected_password_surfaces_as_InvalidCredentials(self, tokens, monkeypatch):
        self.install(monkeypatch, REJECTED)

        with pytest.raises(InvalidCredentials):
            source.bootstrap_login("a@b.c", "wrong", prompt_mfa=lambda: "")

    def test_serving_with_a_stale_token_still_says_AuthExpired(self, tokens, monkeypatch):
        """The other path, unchanged. `_connect` must keep reporting expiry."""
        tokens.write_text('{"stale": true}')
        monkeypatch.setattr(source, "_client", None)
        self.install(monkeypatch, REJECTED)

        with pytest.raises(AuthExpired):
            source._connect()

    def test_no_tokens_at_all_is_still_NotBootstrapped(self, tokens, monkeypatch):
        """Three states, three messages. This one was already right and must
        stay that way."""
        monkeypatch.setattr(source, "_client", None)

        with pytest.raises(NotBootstrapped):
            source._connect()


class TestOnlyGarminGetsToSayItWasThePassword:
    """`GarminConnectAuthenticationError` is raised from roughly fifteen places
    in the library and only two concern credentials. The others are "Invalid
    profile data found", "Not authenticated", "Authentication error", and a bot
    challenge answered with a 401.

    This class exists because the password WAS correct when the form reported a
    login failure. Mapping the whole exception type onto "check your password"
    would have replaced one confidently wrong message with another."""

    from garmin_mcp.source.errors import LoginRejected

    def translate(self, message):
        return source._translate(
            GarminConnectAuthenticationError(message), "/d/t.json", during_login=True
        )

    @pytest.mark.parametrize(
        "message",
        [
            "401 Unauthorized (Invalid Username or Password)",
            "INVALID_USERNAME_PASSWORD",
            "invalid username or password",
        ],
    )
    def test_garmin_saying_so_is_the_only_route_to_a_password_verdict(self, message):
        assert isinstance(self.translate(message), InvalidCredentials)

    @pytest.mark.parametrize(
        "message",
        [
            "Invalid profile data found",
            "Invalid user settings found",
            "Not authenticated",
            "Authentication error",
            "401 Unauthorized",
        ],
    )
    def test_everything_else_reports_what_garmin_said(self, message):
        from garmin_mcp.source.errors import LoginRejected

        result = self.translate(message)

        assert isinstance(result, LoginRejected)
        assert message in str(result)

    def test_it_does_not_accuse_the_password(self):
        from garmin_mcp.source.errors import LoginRejected

        text = str(LoginRejected("Invalid profile data found")).lower()

        assert "did not say the credentials were wrong" in text
        assert "connect.garmin.com" in text

    def test_a_bare_401_is_not_read_as_a_password_problem(self):
        """A bot challenge answered with a 401 carries no INVALID_USERNAME
        marker. Reading it as a typo sends the operator to reset a password that
        was never the problem."""
        from garmin_mcp.source.errors import LoginRejected

        assert isinstance(self.translate("401 Unauthorized"), LoginRejected)
