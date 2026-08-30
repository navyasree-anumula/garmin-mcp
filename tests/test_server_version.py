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


def test_the_build_is_also_in_the_handshake(monkeypatch):
    """`serverInfo.version` is the only build information a client can see
    without calling anything, and it is what the client displays. It was empty:
    a server could show as connected while saying nothing about WHICH build had
    connected -- the exact gap `_server_version` exists to close, left open at
    the cheapest place to read it.

    Observed empty in a real handshake against the published image before this
    was fixed.
    """
    import importlib

    monkeypatch.setenv("GARMIN_MCP_BUILD", "abc1234")
    import garmin_mcp.server as server

    reloaded = importlib.reload(server)
    try:
        assert reloaded.mcp.version == reloaded._server_version()
        assert "abc1234" in reloaded.mcp.version
    finally:
        monkeypatch.delenv("GARMIN_MCP_BUILD", raising=False)
        importlib.reload(server)
