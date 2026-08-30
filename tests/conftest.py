import pytest

from garmin_mcp.source import client as source


@pytest.fixture(autouse=True)
def reset_client_cache():
    """The client is cached for the process lifetime by design (one login per
    session). Tests must not inherit each other's cached client."""
    source._client = None
    yield
    source._client = None


@pytest.fixture
def tokenfile(tmp_path, monkeypatch):
    """A token file that exists. Contents are irrelevant — the fake client never
    parses it; only its existence is checked."""
    path = tmp_path / "garmin_tokens.json"
    path.write_text("{}")
    monkeypatch.setenv("GARMINTOKENS", str(path))
    return path


@pytest.fixture
def no_tokenfile(tmp_path, monkeypatch):
    path = tmp_path / "absent" / "garmin_tokens.json"
    monkeypatch.setenv("GARMINTOKENS", str(path))
    return path
