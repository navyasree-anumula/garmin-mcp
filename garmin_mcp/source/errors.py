"""Our error vocabulary.

The tool layer maps these to MCP errors without importing `garminconnect`, which
keeps the §11 boundary intact. Every message is written to be actionable by
whoever reads it — an agent or a human — because the alternative is a stack trace
in a chat window.
"""

BOOTSTRAP_COMMAND = (
    "docker run -it --rm -v garmin-tokens:/data "
    "ghcr.io/navyasree-anumula/garmin-mcp:latest login"
)


class GarminSourceError(Exception):
    """Base for every error raised by this package."""


class NotBootstrapped(GarminSourceError):
    """No token file. The server has never been given credentials on this machine."""

    def __init__(self, path: str) -> None:
        super().__init__(
            f"No Garmin tokens found at {path}. This server holds tokens only and "
            f"cannot log in by design. Run the bootstrap once on this machine:\n"
            f"    {BOOTSTRAP_COMMAND}"
        )


class AuthExpired(GarminSourceError):
    """A token file exists but Garmin rejected it."""

    def __init__(self, path: str) -> None:
        super().__init__(
            f"Garmin rejected the stored tokens at {path}. They have expired or been "
            f"revoked. Re-run the bootstrap to get new ones:\n"
            f"    {BOOTSTRAP_COMMAND}\n"
            f"Back up the existing token file first — if a captcha blocks the fresh "
            f"login, it is the only way back."
        )


class InvalidCredentials(GarminSourceError):
    """Garmin rejected the email and password themselves.

    Distinct from `AuthExpired`, and the distinction is not pedantry: the two
    arrive as the SAME library exception, and the only thing separating them is
    which of our own calls was in flight. Telling somebody their stored tokens
    expired when they have just typed a password is advice that cannot be acted
    on -- it names a file they do not have and tells them to run the bootstrap
    they are already running.

    It is also the one authentication failure that is NOT a lockout. Getting
    that wrong in the other direction is just as bad: telling somebody to wait
    several minutes after a typo teaches them the tool is flaky.
    """

    def __init__(self, detail: str = "") -> None:
        message = (
            "Garmin rejected this email and password. Check them at "
            "connect.garmin.com — they are the same credentials.\n"
            "This is not a lockout and the tokens on this machine are "
            "untouched, so it is safe to correct the typo and try again. Do "
            "not hammer it: repeated failures can still attract bot protection."
        )
        if detail:
            message += f"\n({detail})"
        super().__init__(message)


class RateLimited(GarminSourceError):
    """Garmin returned 429.

    Raised for both the login path and the data path, which reach us wearing
    different exception types -- see `_translate` in `client.py`.
    """

    def __init__(self, detail: str | None = None) -> None:
        message = (
            "Garmin is rate limiting this IP (HTTP 429). Wait several minutes. "
            "Do not retry in a loop — repeated attempts extend the block."
        )
        if detail:
            message += f" ({detail})"
        super().__init__(message)


class SourceUnavailable(GarminSourceError):
    """Garmin could not be reached, or returned something unusable."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Could not reach Garmin Connect: {detail}")
