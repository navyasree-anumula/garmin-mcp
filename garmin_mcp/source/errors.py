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
