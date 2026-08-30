"""HTML for the two pages, as plain strings.

No template engine. `jinja2` is not in `requirements.lock` and the lockfile is
the shipped artifact (docs/SCOPE.md §8) -- adding a dependency to the production
image for two pages is a bad trade. Everything dynamic goes through
`html.escape`.

**Nothing in this module ever renders a password or a token.** The status page
reports that tokens exist and when they were written, never what they contain
(§7a). The login form is re-rendered on failure with the email preserved and the
password field always empty.
"""

from __future__ import annotations

from html import escape

_STYLE = """
:root { color-scheme: light dark; }
body { font: 15px/1.55 system-ui, sans-serif; max-width: 34rem;
       margin: 3rem auto; padding: 0 1.25rem; }
h1 { font-size: 1.25rem; margin-bottom: .25rem; }
p.sub { color: #666; margin-top: 0; }
label { display: block; margin: 1rem 0 .25rem; font-weight: 600; }
input { width: 100%; padding: .5rem; font: inherit; box-sizing: border-box; }
button { margin-top: 1.25rem; padding: .55rem 1.1rem; font: inherit; }
button[disabled] { opacity: .5; cursor: not-allowed; }
table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
td { padding: .35rem .5rem; border-bottom: 1px solid #8883; vertical-align: top; }
td:first-child { color: #666; white-space: nowrap; width: 11rem; }
.bad { color: #b00; } .good { color: #060; }
.note { background: #8881; padding: .75rem 1rem; margin-top: 1.5rem;
        border-left: 3px solid #888; }
nav { margin-bottom: 1.5rem; font-size: .9rem; }
"""


def _document(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )


def _nav(current: str) -> str:
    parts = []
    for href, label in (("/", "Status"), ("/login", "Sign in to Garmin")):
        parts.append(
            f"<strong>{escape(label)}</strong>"
            if href == current
            else f'<a href="{href}">{escape(label)}</a>'
        )
    return "<nav>" + " &middot; ".join(parts) + "</nav>"


def status_page(rows: list[tuple[str, str, str]], version: str) -> str:
    """`rows` is (label, value, css_class); no value here is ever a secret."""
    cells = "".join(
        f"<tr><td>{escape(label)}</td>"
        f'<td class="{escape(css)}">{escape(value)}</td></tr>'
        for label, value, css in rows
    )
    body = (
        f"{_nav('/')}"
        "<h1>garmin-mcp</h1>"
        f"<p class=sub>build {escape(version)}</p>"
        f"<table>{cells}</table>"
        "<div class=note>This page makes <strong>no</strong> call to Garmin, so it "
        "costs nothing to reload and cannot itself be rate limited. It reports that "
        "tokens exist, never what they contain. Whether they still <em>work</em> is "
        "what the <code>garmin_auth_status</code> tool answers.</div>"
    )
    return _document("garmin-mcp status", body)


def login_page(
    csrf_token: str,
    email: str = "",
    error: str = "",
    *,
    disable_submit: bool = False,
    advice: str = "",
) -> str:
    """The credential form.

    `disable_submit` is not cosmetic. On a 429 or a captcha the single most
    damaging thing the operator can do is press the button again -- repeated
    attempts extend the block and a captcha lockout has no workaround by design
    (docs/SCOPE.md §9, §12.1). A form that invites the retry is a form that
    causes the outage.
    """
    banner = f'<p class="bad"><strong>{escape(error)}</strong></p>' if error else ""
    note = f"<div class=note>{advice}</div>" if advice else ""
    attr = " disabled" if disable_submit else ""
    body = (
        f"{_nav('/login')}"
        "<h1>Sign in to Garmin Connect</h1>"
        "<p class=sub>Used once, exchanged for tokens, never stored.</p>"
        f"{banner}"
        '<form method="post" action="/login" autocomplete="off">'
        f'<input type="hidden" name="csrf_token" value="{escape(csrf_token)}">'
        '<label for="email">Email</label>'
        f'<input id="email" name="email" type="email" required value="{escape(email)}">'
        '<label for="password">Password</label>'
        # Never carries a value. A re-rendered form that helpfully preserves the
        # password puts it in the page source and the browser's back cache.
        '<input id="password" name="password" type="password" required>'
        f"<button type=submit{attr}>Sign in</button>"
        "</form>"
        f"{note}"
    )
    return _document("Sign in — garmin-mcp", body)


def success_page(who: str) -> str:
    body = (
        f"{_nav('')}"
        "<h1>Tokens written</h1>"
        f"<p>Authenticated as <strong>{escape(who)}</strong>.</p>"
        "<div class=note><strong>Back up the token volume now.</strong> If a captcha "
        "ever blocks a fresh login there is no workaround, and this file is the only "
        "way back:<br><br><code>docker run --rm -v garmin-tokens:/data "
        "-v &quot;${PWD}:/backup&quot; alpine cp /data/garmin_tokens.json /backup/"
        "</code><br><br>Treat the copy as a live credential. Do not put it in "
        "OneDrive or Dropbox.</div>"
    )
    return _document("Signed in — garmin-mcp", body)


# Shown when Garmin asks for a code. The web flow does not implement the
# two-step exchange; the CLI does, and pointing at a route that exists beats
# pointing at one that does not (docs/SCOPE.md §4).
MFA_ADVICE = (
    "<strong>This account now requires an MFA code.</strong> The web form does not "
    "handle that yet. Run the CLI bootstrap instead, which prompts for the code:"
    "<br><br><code>docker run -it --rm -v garmin-tokens:/data "
    "ghcr.io/navyasree-anumula/garmin-mcp:latest login</code>"
)

RATE_LIMIT_ADVICE = (
    "<strong>Wait. Do not retry.</strong> Repeated attempts extend the block, and if "
    "this becomes a captcha lockout there is no workaround by design. The submit "
    "button is disabled deliberately. Come back in several minutes."
)
