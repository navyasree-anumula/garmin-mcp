"""The running build must be identifiable from inside a conversation.

`docker run` does not re-pull a moving tag: a client config pinned to `latest`
keeps running whichever image was pulled last, indefinitely, with nothing
anywhere saying so. Reporting the version in auth status is what makes "am I on
the current build?" answerable without shell access to the host.
"""

from garmin_mcp import __version__
from garmin_mcp.server import AuthStatus, _server_version


def test_version_alone_when_not_ci_built(monkeypatch):
    monkeypatch.delenv("GARMIN_MCP_BUILD", raising=False)

    assert _server_version() == __version__


def test_build_ref_is_appended_when_stamped(monkeypatch):
    monkeypatch.setenv("GARMIN_MCP_BUILD", "aadca67")

    version = _server_version()

    assert version.startswith(__version__)
    assert "aadca67" in version


def test_blank_build_ref_does_not_produce_a_dangling_suffix(monkeypatch):
    """An unstamped local build sets the ARG to an empty string, not nothing."""
    monkeypatch.setenv("GARMIN_MCP_BUILD", "   ")

    assert _server_version() == __version__


def test_auth_status_exposes_the_version_field():
    assert "server_version" in AuthStatus.model_fields
