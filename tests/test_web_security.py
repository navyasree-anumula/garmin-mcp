"""The web UI is a new credential surface, so its defences are the tests.

The threat is not somebody on the network. It is the browser already running on
this machine: any page you visit can issue requests to `127.0.0.1`, and DNS
rebinding defeats a naive origin check by making a hostname the browser already
trusts resolve to loopback (docs/SCOPE.md §7a).

Every test here is written so it can say no. Each of the two load-bearing checks
-- the Host allowlist and the CSRF token -- was also run with its guard removed
and observed to fail, because a security test that has never been seen to
discriminate is not evidence, it is decoration.
"""

import logging

import pytest
from starlette.testclient import TestClient

from garmin_mcp.web.app import SESSION_COOKIE, create_app, default_allowed_hosts

ALLOWED = default_allowed_hosts(8765)
GOOD_HOST = "127.0.0.1:8765"
GOOD_ORIGIN = "http://127.0.0.1:8765"

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def calls():
    return []


@pytest.fixture
def client(calls):
    app = create_app(
        allowed_hosts=ALLOWED,
        bootstrap=lambda email, password: calls.append((email, password)) or "Test User",
        status_rows=lambda: [("Token file", "/data/garmin_tokens.json", "")],
        version=lambda: "0.0.0+test",
    )
    return TestClient(app)


def _csrf(client):
    """Fetch the form and return (csrf token, session cookie)."""
    response = client.get("/login", headers={"Host": GOOD_HOST})
    assert response.status_code == 200
    marker = 'name="csrf_token" value="'
    start = response.text.index(marker) + len(marker)
    return response.text[start : response.text.index('"', start)]


class TestHostAllowlist:
    """DNS rebinding: an attacker's hostname resolves to 127.0.0.1, so the
    packet is genuinely local and only the Host header still tells the truth."""

    @pytest.mark.parametrize(
        "host",
        [
            "evil.example",
            "evil.example:8765",
            "garmin-mcp.attacker.test:8765",
            "127.0.0.1.nip.io:8765",   # resolves to loopback, is not us
            "0.0.0.0:8765",
            "",
        ],
    )
    def test_foreign_host_header_is_refused(self, client, host):
        assert client.get("/", headers={"Host": host}).status_code == 403

    @pytest.mark.parametrize("host", ALLOWED)
    def test_expected_hosts_are_served(self, client, host):
        assert client.get("/", headers={"Host": host}).status_code == 200

    def test_refusal_happens_before_routing(self, client):
        """A 404 would still confirm a server is listening here."""
        response = client.get("/no-such-page", headers={"Host": "evil.example"})

        assert response.status_code == 403

    def test_the_login_form_is_not_reachable_from_a_foreign_host(self, client):
        assert client.get("/login", headers={"Host": "evil.example"}).status_code == 403


class TestNoCors:
    """`Access-Control-Allow-Origin` on a page that mints credentials would hand
    every site in the browser exactly what the Host and Origin checks deny."""

    def test_no_cors_headers_on_any_response(self, client):
        for response in (
            client.get("/", headers={"Host": GOOD_HOST}),
            client.get("/login", headers={"Host": GOOD_HOST}),
            client.get("/", headers={"Host": "evil.example"}),
        ):
            assert not any(
                header.lower().startswith("access-control-")
                for header in response.headers
            ), dict(response.headers)

    def test_preflight_is_not_answered(self, client):
        response = client.options(
            "/login",
            headers={"Host": GOOD_HOST, "Origin": "http://evil.example"},
        )

        assert "access-control-allow-origin" not in response.headers


class TestSameOrigin:
    def test_cross_origin_post_is_refused(self, client, calls):
        token = _csrf(client)

        response = client.post(
            "/login",
            headers={"Host": GOOD_HOST, "Origin": "http://evil.example"},
            data={"csrf_token": token, "email": "a@b.c", "password": PASSWORD},
        )

        assert response.status_code == 403
        assert calls == []   # never reached Garmin

    def test_post_with_no_origin_and_no_referer_is_refused(self, client, calls):
        """Browsers send Origin on form POSTs. A request carrying neither that
        nor a Referer is refused rather than trusted -- on a form that exchanges
        a password, that is the right default."""
        token = _csrf(client)

        response = client.post(
            "/login",
            headers={"Host": GOOD_HOST},
            data={"csrf_token": token, "email": "a@b.c", "password": PASSWORD},
        )

        assert response.status_code == 403
        assert calls == []

    def test_same_origin_referer_is_accepted(self, client, calls):
        token = _csrf(client)

        response = client.post(
            "/login",
            headers={"Host": GOOD_HOST, "Referer": f"{GOOD_ORIGIN}/login"},
            data={"csrf_token": token, "email": "a@b.c", "password": PASSWORD},
        )

        assert response.status_code == 200
        assert len(calls) == 1

    def test_a_referer_that_merely_starts_with_our_origin_is_refused(self, client, calls):
        """`http://127.0.0.1:8765.evil.example` starts with our origin as a
        string and is a different site entirely."""
        token = _csrf(client)

        response = client.post(
            "/login",
            headers={"Host": GOOD_HOST, "Referer": f"{GOOD_ORIGIN}.evil.example/login"},
            data={"csrf_token": token, "email": "a@b.c", "password": PASSWORD},
        )

        assert response.status_code == 403
        assert calls == []


class TestCsrf:
    def test_missing_token_is_refused(self, client, calls):
        client.get("/login", headers={"Host": GOOD_HOST})

        response = client.post(
            "/login",
            headers={"Host": GOOD_HOST, "Origin": GOOD_ORIGIN},
            data={"email": "a@b.c", "password": PASSWORD},
        )

        assert response.status_code == 403
        assert calls == []

    def test_forged_token_is_refused(self, client, calls):
        _csrf(client)

        response = client.post(
            "/login",
            headers={"Host": GOOD_HOST, "Origin": GOOD_ORIGIN},
            data={"csrf_token": "x" * 43, "email": "a@b.c", "password": PASSWORD},
        )

        assert response.status_code == 403
        assert calls == []

    def test_a_token_from_another_session_is_refused(self, client, calls):
        """The token is bound to the session cookie (§7a), so one lifted from a
        different browser session must not work in this one."""
        other = TestClient(client.app)
        stolen = _csrf(other)
        _csrf(client)   # this session has its own, different token

        response = client.post(
            "/login",
            headers={"Host": GOOD_HOST, "Origin": GOOD_ORIGIN},
            data={"csrf_token": stolen, "email": "a@b.c", "password": PASSWORD},
        )

        assert response.status_code == 403
        assert calls == []

    def test_the_matching_token_is_accepted(self, client, calls):
        token = _csrf(client)

        response = client.post(
            "/login",
            headers={"Host": GOOD_HOST, "Origin": GOOD_ORIGIN},
            data={"csrf_token": token, "email": "a@b.c", "password": PASSWORD},
        )

        assert response.status_code == 200
        assert len(calls) == 1

    def test_session_cookie_is_httponly_and_samesite_strict(self, client):
        response = client.get("/login", headers={"Host": GOOD_HOST})

        cookie = next(
            value for key, value in response.headers.items()
            if key.lower() == "set-cookie" and value.startswith(SESSION_COOKIE)
        )

        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie.replace("Strict", "strict")


class TestResponseHygiene:
    def test_pages_carrying_a_token_are_not_cacheable(self, client):
        response = client.get("/login", headers={"Host": GOOD_HOST})

        assert "no-store" in response.headers["cache-control"]

    def test_framing_and_csp_are_locked_down(self, client):
        response = client.get("/", headers={"Host": GOOD_HOST})

        assert response.headers["x-frame-options"] == "DENY"
        assert "form-action 'self'" in response.headers["content-security-policy"]
        assert "default-src 'none'" in response.headers["content-security-policy"]


class TestTheReferrerPolicyDoesNotBreakTheOriginCheck:
    """A security header that disables a security check.

    Per Fetch, `Origin` on a non-CORS POST is set to `null` when the page's
    referrer policy is `no-referrer` -- and no `Referer` is sent either. So
    shipping `Referrer-Policy: no-referrer` made the same-origin check below
    unsatisfiable: the login form refused its own submission with "this request
    did not come from this page".

    It shipped because every test here sets `Origin` by hand. `TestClient` does
    not derive it from a referrer policy the way a browser does, so no test at
    this level could have caught it, and none of them noticed the two headers
    were in conflict. A real browser found it on first use.
    """

    FORBIDDEN = {"no-referrer"}

    def test_the_policy_we_send_still_permits_a_real_origin(self, client):
        response = client.get("/login", headers={"Host": GOOD_HOST})

        assert response.headers["referrer-policy"] not in self.FORBIDDEN

    def test_an_origin_of_null_is_refused(self, client, calls):
        """What a `no-referrer` page, a sandboxed iframe, or a cross-origin
        redirect sends. It is never us, and accepting it to "fix" the bug above
        would have removed the check instead of repairing it."""
        token = _csrf(client)

        response = client.post(
            "/login",
            headers={"Host": GOOD_HOST, "Origin": "null"},
            data={"csrf_token": token, "email": "a@b.c", "password": PASSWORD},
        )

        assert response.status_code == 403
        assert calls == []

    def test_the_policy_still_withholds_the_referer_from_third_parties(self, client):
        """`same-origin` is the loosest policy that works and no looser: our own
        requests carry a Referer, nobody else's does."""
        response = client.get("/login", headers={"Host": GOOD_HOST})

        assert response.headers["referrer-policy"] == "same-origin"


class TestThePasswordNeverEscapes:
    """§4's invariant, checked at the only place the password exists."""

    def test_it_is_not_echoed_into_the_success_page(self, client):
        token = _csrf(client)

        response = client.post(
            "/login",
            headers={"Host": GOOD_HOST, "Origin": GOOD_ORIGIN},
            data={"csrf_token": token, "email": "a@b.c", "password": PASSWORD},
        )

        assert PASSWORD not in response.text

    def test_it_is_not_echoed_back_into_a_re_rendered_form(self, calls):
        """A form that helpfully repopulates the password puts it in the page
        source and the browser's back/forward cache."""
        from garmin_mcp.source.errors import SourceUnavailable

        def failing(email, password):
            raise SourceUnavailable("nope")

        client = TestClient(
            create_app(allowed_hosts=ALLOWED, bootstrap=failing,
                       status_rows=lambda: [], version=lambda: "t")
        )
        token = _csrf(client)

        response = client.post(
            "/login",
            headers={"Host": GOOD_HOST, "Origin": GOOD_ORIGIN},
            data={"csrf_token": token, "email": "a@b.c", "password": PASSWORD},
        )

        assert response.status_code == 400
        assert PASSWORD not in response.text
        assert "a@b.c" in response.text      # the email does come back

    def test_it_is_not_logged(self, client, caplog):
        token = _csrf(client)

        with caplog.at_level(logging.DEBUG):
            client.post(
                "/login",
                headers={"Host": GOOD_HOST, "Origin": GOOD_ORIGIN},
                data={"csrf_token": token, "email": "a@b.c", "password": PASSWORD},
            )

        assert PASSWORD not in caplog.text

    def test_a_refused_cross_origin_post_does_not_log_the_body(self, client, caplog):
        token = _csrf(client)

        with caplog.at_level(logging.DEBUG):
            client.post(
                "/login",
                headers={"Host": GOOD_HOST, "Origin": "http://evil.example"},
                data={"csrf_token": token, "email": "a@b.c", "password": PASSWORD},
            )

        assert PASSWORD not in caplog.text
