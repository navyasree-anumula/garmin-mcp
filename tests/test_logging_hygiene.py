"""Health data must never reach the logs (docs/SCOPE.md §8).

garminconnect logs the entire response body at DEBUG on any API error:

    _LOGGER.debug("API error response: status=%s body=%r", resp.status_code, resp.text)

On a sleep or heart-rate endpoint that body IS the health data. Nothing in our
code calls that line -- the only thing standing between us and health data on
disk is the log level we set at startup. That makes the level load-bearing, so
it gets a regression test rather than a comment.
"""

import logging

import pytest

from garmin_mcp.cli import _configure_logging

# The library logger that carries the body-logging call.
LIBRARY_LOGGER = "garminconnect.client"


@pytest.fixture(autouse=True)
def restore_logging():
    """_configure_logging uses force=True, which tears down root handlers --
    including the ones pytest installs. Put them back."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


def test_library_debug_is_suppressed():
    _configure_logging()

    assert not logging.getLogger(LIBRARY_LOGGER).isEnabledFor(logging.DEBUG)


def test_configuration_wins_over_an_earlier_setup():
    """The failure this guards against is silent.

    basicConfig does nothing when root already has handlers. If some import
    configures logging at DEBUG before we run, a basicConfig without force=True
    returns quietly having changed nothing, and every API error starts writing
    response bodies to stderr.
    """
    root = logging.getLogger()
    root.addHandler(logging.NullHandler())
    root.setLevel(logging.DEBUG)
    assert logging.getLogger(LIBRARY_LOGGER).isEnabledFor(logging.DEBUG)  # armed

    _configure_logging()

    assert root.level == logging.WARNING
    assert not logging.getLogger(LIBRARY_LOGGER).isEnabledFor(logging.DEBUG)


def test_the_check_can_actually_fail():
    """Validate the instrument both ways.

    An isEnabledFor assertion that can never be True would pass the tests above
    while proving nothing. This shows it does move.
    """
    _configure_logging()
    assert not logging.getLogger(LIBRARY_LOGGER).isEnabledFor(logging.DEBUG)

    logging.getLogger().setLevel(logging.DEBUG)
    assert logging.getLogger(LIBRARY_LOGGER).isEnabledFor(logging.DEBUG)
