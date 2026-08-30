"""What the login form does with each way Garmin can say no.

The dangerous failure here is not a bad error message. It is a form that invites
the operator to press the button again: repeated attempts extend a 429, and if
it becomes a captcha lockout there is no workaround by design and no token
backup to fall back on (docs/SCOPE.md §9, §12.1).
"""

import pytest
from starlette.testclient import TestClient

from garmin_mcp.source.errors import GarminSourceError, RateLimited, SourceUnavailable
from garmin_mcp.web.app import create_app, default_allowed_hosts

ALLOWED = default_allowed_hosts(8765)
GOOD_HOST = "127.0.0.1:8765"
GOOD_ORIGIN = "http://127.0.0.1:8765"


def build(bootstrap):
    return TestClient(
        create_app(
            allowed_hosts=ALLOWED,
            bootstrap=bootstrap,
            status_rows=lambda: [],
            version=lambda: "0.0.0+test",
        )
    )


# Assert on the button markup, never on the bare word "disabled": the stylesheet
# contains a `button[disabled]` rule, so a substring check passes whether or not
# the attribute was ever rendered. The negative test below is what caught that,
# which is the entire argument for writing negative tests.
DISABLED_BUTTON = "<button type=submit disabled>"
LIVE_BUTTON = "<button type=submit>"


def submit(client, *, email="mahi@example.com", password="hunter2"):
    response = client.get("/login", headers={"Host": GOOD_HOST})
    marker = 'name="csrf_token" value="'
    start = response.text.index(marker) + len(marker)
    token = response.text[start : response.text.index('"', start)]

    return client.post(
        "/login",
        headers={"Host": GOOD_HOST, "Origin": GOOD_ORIGIN},
        data={"csrf_token": token, "email": email, "password": password},
    )


class TestSuccess:
    def test_the_submitted_credentials_reach_the_source_layer_once(self):
        seen = []
        client = build(lambda e, p: seen.append((e, p)) or "Mahi")

        response = submit(client)

        assert response.status_code == 200
        assert seen == [("mahi@example.com", "hunter2")]

    def test_the_success_page_names_the_account_and_the_backup(self):
        client = build(lambda e, p: "Mahi")

        response = submit(client)

        assert "Mahi" in response.text
        assert "Back up the token volume" in response.text
        # Without a backup a captcha lockout is unrecoverable, so this is the
        # single most important sentence on the page.
        assert "garmin_tokens.json" in response.text


class TestRefusalsThatMustNotInviteARetry:
    def test_rate_limit_disables_the_submit_button(self):
        client = build(lambda e, p: (_ for _ in ()).throw(RateLimited()))

        response = submit(client)

        assert response.status_code == 400
        assert DISABLED_BUTTON in response.text
        assert "Do not retry" in response.text

    def test_a_generic_source_error_also_disables_it(self):
        """A captcha arrives as a plain source error, and it is the case where
        retrying does the most damage."""
        client = build(lambda e, p: (_ for _ in ()).throw(SourceUnavailable("captcha")))

        response = submit(client)

        assert response.status_code == 400
        assert DISABLED_BUTTON in response.text

    def test_mfa_points_at_the_cli_which_actually_implements_it(self):
        """§4: an error must name a route that exists. The web flow cannot
        collect a code; the CLI can."""
        from garmin_mcp.web.app import MfaRequired

        client = build(lambda e, p: (_ for _ in ()).throw(MfaRequired()))

        response = submit(client)

        assert response.status_code == 400
        assert "login" in response.text
        assert "docker run" in response.text
        assert DISABLED_BUTTON in response.text


class TestOrdinaryMistakes:
    """A wrong password is not a lockout, so the form must stay usable."""

    @pytest.mark.parametrize("email,password", [("", "hunter2"), ("a@b.c", "")])
    def test_missing_fields_never_reach_garmin(self, email, password):
        seen = []
        client = build(lambda e, p: seen.append(1) or "x")

        response = submit(client, email=email, password=password)

        assert response.status_code == 400
        assert seen == []

    def test_a_missing_field_leaves_the_button_usable(self):
        client = build(lambda e, p: "x")

        response = submit(client, password="")

        assert LIVE_BUTTON in response.text
        assert DISABLED_BUTTON not in response.text

    def test_the_retry_form_carries_a_fresh_working_token(self):
        """The re-rendered form must be submittable. A stale token here would
        turn one typo into a dead page."""
        attempts = []

        def bootstrap(email, password):
            attempts.append(password)
            if len(attempts) == 1:
                raise SourceUnavailable("first try fails")
            return "Mahi"

        client = build(bootstrap)
        first = submit(client)
        assert first.status_code == 400

        marker = 'name="csrf_token" value="'
        start = first.text.index(marker) + len(marker)
        token = first.text[start : first.text.index('"', start)]

        second = client.post(
            "/login",
            headers={"Host": GOOD_HOST, "Origin": GOOD_ORIGIN},
            data={"csrf_token": token, "email": "a@b.c", "password": "hunter2"},
        )

        assert second.status_code == 200
        assert len(attempts) == 2


class TestErrorTextIsOurs:
    def test_the_source_layer_message_is_shown_verbatim(self):
        """errors.py is written to be read by a human and never contains a
        secret -- its messages are built from paths."""
        client = build(
            lambda e, p: (_ for _ in ()).throw(GarminSourceError("something specific"))
        )

        response = submit(client)

        assert "something specific" in response.text

    def test_html_in_an_error_is_escaped(self):
        client = build(
            lambda e, p: (_ for _ in ()).throw(SourceUnavailable("<script>x</script>"))
        )

        response = submit(client)

        assert "<script>x</script>" not in response.text
        assert "&lt;script&gt;" in response.text
