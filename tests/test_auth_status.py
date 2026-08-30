"""Tests for the source boundary's error vocabulary and happy path.

The Garmin client is faked at the `source/` boundary — these never touch the
network and never need a token.
"""

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp.source import client as source
from garmin_mcp.source.errors import (
    AuthExpired,
    NotBootstrapped,
    RateLimited,
)


class FakeGarmin:
    """Stands in for garminconnect.Garmin. Records how it was constructed and
    how many times login() was attempted."""

    instances: list["FakeGarmin"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.login_calls = 0
        self.raises = None
        self.display_name = "mahi"
        self.full_name = "Mahidhar"
        self.unit_system = "metric"
        FakeGarmin.instances.append(self)

    def login(self, tokenstore=None):
        self.login_calls += 1
        if self.raises is not None:
            raise self.raises
        return None, None

    def get_full_name(self):
        return self.full_name

    def get_unit_system(self):
        return self.unit_system


@pytest.fixture
def fake_garmin(monkeypatch):
    FakeGarmin.instances = []

    def factory(**kwargs):
        inst = FakeGarmin(**kwargs)
        inst.raises = factory.raises
        return inst

    factory.raises = None
    monkeypatch.setattr(source, "Garmin", factory)
    return factory


def test_missing_token_file_is_not_bootstrapped(no_tokenfile, fake_garmin):
    """A server that was never bootstrapped must say so, and name the command.

    This is distinct from an expired token on purpose: garminconnect reports both
    as GarminConnectAuthenticationError, so without the explicit existence check
    the operator gets told to re-authenticate when they never authenticated.
    """
    with pytest.raises(NotBootstrapped) as excinfo:
        source.auth_status()

    message = str(excinfo.value)
    assert "login" in message
    assert "docker run" in message
    assert str(no_tokenfile) in message
    # It must not have tried to talk to Garmin at all.
    assert FakeGarmin.instances == []


def test_rejected_token_is_auth_expired(tokenfile, fake_garmin):
    fake_garmin.raises = GarminConnectAuthenticationError("401 Unauthorized")

    with pytest.raises(AuthExpired) as excinfo:
        source.auth_status()

    message = str(excinfo.value)
    assert "rejected" in message.lower()
    assert "Back up" in message


def test_missing_and_expired_produce_different_errors(
    tmp_path, monkeypatch, fake_garmin
):
    """The two failures must stay distinguishable — that is the whole point of
    the explicit file check."""
    absent = tmp_path / "gone.json"
    monkeypatch.setenv("GARMINTOKENS", str(absent))
    with pytest.raises(NotBootstrapped):
        source.auth_status()

    source._client = None
    present = tmp_path / "there.json"
    present.write_text("{}")
    monkeypatch.setenv("GARMINTOKENS", str(present))
    fake_garmin.raises = GarminConnectAuthenticationError("401 Unauthorized")
    with pytest.raises(AuthExpired):
        source.auth_status()


def test_rate_limit_is_surfaced_and_not_retried(tokenfile, fake_garmin):
    fake_garmin.raises = GarminConnectTooManyRequestsError("429")

    with pytest.raises(RateLimited) as excinfo:
        source.auth_status()

    assert "do not retry" in str(excinfo.value).lower()
    # Exactly one client, exactly one login attempt. Retrying into a 429 extends
    # the block.
    assert len(FakeGarmin.instances) == 1
    assert FakeGarmin.instances[0].login_calls == 1


def test_happy_path_returns_populated_status(tokenfile, fake_garmin):
    status = source.auth_status()

    assert status.authenticated is True
    assert status.display_name == "mahi"
    assert status.full_name == "Mahidhar"
    assert status.unit_system == "metric"
    assert status.tokens_path == str(tokenfile)
    assert status.tokens_modified_utc != "unknown"


def test_serving_client_is_built_without_credentials(tokenfile, fake_garmin):
    """The absence of email/password is what makes garminconnect re-raise instead
    of silently falling back to a full login (docs/SCOPE.md §4)."""
    source.auth_status()

    assert len(FakeGarmin.instances) == 1
    kwargs = FakeGarmin.instances[0].kwargs
    assert "email" not in kwargs
    assert "password" not in kwargs


def test_client_is_reused_across_calls(tokenfile, fake_garmin):
    """login() is a real round-trip to Garmin. One per process, not one per call."""
    source.auth_status()
    source.auth_status()
    source.auth_status()

    assert len(FakeGarmin.instances) == 1
    assert FakeGarmin.instances[0].login_calls == 1
