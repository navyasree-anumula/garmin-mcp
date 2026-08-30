import pytest

from garmin_mcp.source import client as source


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
    # Same list object FakeGarmin appends to, so tests can assert on the
    # instances without importing the class.
    factory.instances = FakeGarmin.instances
    monkeypatch.setattr(source, "Garmin", factory)
    return factory


@pytest.fixture(autouse=True)
def fast_rate_limit(monkeypatch):
    """Keep the real budget out of tests that are not about the budget.

    The shipped default is one request every two seconds. Any test making more
    than a burst of Garmin calls would otherwise sit in a sleep, and a suite
    that is slow for a reason nobody remembers gets weakened later.
    tests/test_ratelimit.py overrides this with its own values.
    """
    monkeypatch.setenv("GARMIN_RATE_PER_SEC", "10000")
    monkeypatch.setenv("GARMIN_RATE_BURST", "10000")


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
