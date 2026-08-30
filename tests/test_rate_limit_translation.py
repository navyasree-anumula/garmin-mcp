"""A rate limit must be recognisable no matter which door it comes through.

garminconnect raises GarminConnectTooManyRequestsError only from its LOGIN
paths. On a data request, client._run_request maps every status >= 400 except
404 onto GarminConnectConnectionError with no 429 branch at all, so a rate limit
arrives looking exactly like a connectivity blip. Untranslated, the agent reads
"could not reach Garmin" as transient and retries -- the one behaviour
docs/SCOPE.md §6 forbids, against an IP that is already being throttled.
"""

import pytest
from garminconnect import (
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp.source import client as source
from garmin_mcp.source.errors import RateLimited, SourceUnavailable


def test_login_path_429_is_rate_limited(tokenfile, fake_garmin):
    """The typed exception, raised by the login strategies."""
    fake_garmin.raises = GarminConnectTooManyRequestsError("429")

    with pytest.raises(RateLimited) as excinfo:
        source.auth_status()

    assert "do not retry" in str(excinfo.value).lower()


def test_data_path_429_is_rate_limited(tokenfile, fake_garmin):
    """The untyped one, raised by _run_request for any data endpoint."""
    fake_garmin.raises = GarminConnectConnectionError(
        "API Error 429 - Too Many Requests"
    )

    with pytest.raises(RateLimited) as excinfo:
        source.auth_status()

    message = str(excinfo.value)
    assert "do not retry" in message.lower()
    # Names which door it came through, so the operator can tell a throttled
    # login from a throttled read.
    assert "data request" in message


def test_data_path_429_does_not_retry(tokenfile, fake_garmin):
    fake_garmin.raises = GarminConnectConnectionError("API Error 429")

    with pytest.raises(RateLimited):
        source.auth_status()

    assert len(fake_garmin.instances) == 1
    assert fake_garmin.instances[0].login_calls == 1


def test_other_connection_errors_are_not_mistaken_for_rate_limits(
    tokenfile, fake_garmin
):
    """The other half of the instrument: prove it can say no.

    A check that returns RateLimited for everything would pass the test above
    while being useless. A genuine outage must stay SourceUnavailable, or the
    operator gets told to wait out a rate limit that does not exist.
    """
    fake_garmin.raises = GarminConnectConnectionError("API Error 503 - Bad Gateway")

    with pytest.raises(SourceUnavailable) as excinfo:
        source.auth_status()

    assert not isinstance(excinfo.value, RateLimited)


def test_429_inside_another_status_is_not_a_false_positive(tokenfile, fake_garmin):
    """'429' as a substring elsewhere must not trip the match."""
    fake_garmin.raises = GarminConnectConnectionError("API Error 500 - request 4291")

    with pytest.raises(SourceUnavailable) as excinfo:
        source.auth_status()

    assert not isinstance(excinfo.value, RateLimited)
