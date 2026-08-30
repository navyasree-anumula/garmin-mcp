"""Cross-process serialisation of Garmin access.

Two things forced this module.

First, the serving container is not a singleton. `client._connect` caches one
logged-in client per process, and the comment there used to claim that under
stdio a process is a session. Measured 2026-08-30: Claude Desktop spawns TWO
containers for a single configured stdio server, both long-lived, sharing one
token volume.

Second, garminconnect rewrites the token file on its own initiative.
`client._run_request` begins every data request with

    if self.is_authenticated and self._token_expires_soon():
        self._refresh_session()

and a refresh dumps the new tokens to disk. The write is atomic and guarded --
but by `self._token_lock`, a `threading.Lock`, which means nothing whatsoever
between two containers. So two processes can refresh the same credential
concurrently, and the loser's rotated refresh token is the one that survives.

**The lock must not be taken on the token file itself.** `dump()` writes a
temporary file and `replace()`s it, so the token file gets a NEW INODE on every
refresh. `flock` binds to the open file description, not the path: a lock held
on the token file would, after the first refresh, be held on an unlinked inode
that nothing else opens. It would look healthy and exclude nobody. Hence a
separate, never-replaced lock file.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import GarminSourceError

logger = logging.getLogger(__name__)

try:  # pragma: no cover - platform dependent
    import fcntl

    _HAVE_FLOCK = True
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
    _HAVE_FLOCK = False

LOCK_FILENAME = ".garmin.lock"
DEFAULT_TIMEOUT_S = 30.0
_POLL_INTERVAL_S = 0.05

# flock is per open file description, so a second open() in the SAME process
# would block against the first. Nested acquisition has to be counted rather
# than re-locked, or a source function calling another one deadlocks itself.
_local = threading.local()

_warned_no_flock = False


class LockTimeout(GarminSourceError):
    """Another process held the Garmin lock for too long."""

    def __init__(self, path: str, timeout: float) -> None:
        super().__init__(
            f"Timed out after {timeout:.0f}s waiting for exclusive Garmin access "
            f"({path}). Another instance of this server is busy. This resolves "
            f"itself; retrying once is reasonable."
        )


def lock_path_for(tokens_path: str) -> Path:
    """The lock file lives beside the token file, on the same shared volume.

    Beside, never on: see the module docstring on inode replacement.
    """
    return Path(tokens_path).expanduser().parent / LOCK_FILENAME


@contextmanager
def file_lock(path: Path, timeout: float = DEFAULT_TIMEOUT_S) -> Iterator[None]:
    """Exclusive advisory lock on `path`, across processes.

    NOT re-entrant: a second call in the same process opens a second file
    description and blocks against the first. Callers that may nest want
    `garmin_lock`, which counts depth.
    """
    if not _HAVE_FLOCK:  # pragma: no cover - platform dependent
        yield
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                # Blocking flock cannot be interrupted with a deadline, so poll.
                if time.monotonic() >= deadline:
                    raise LockTimeout(str(path), timeout) from None
                time.sleep(_POLL_INTERVAL_S)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def garmin_lock(
    tokens_path: str, timeout: float = DEFAULT_TIMEOUT_S
) -> Iterator[None]:
    """Hold exclusive Garmin access for this token volume.

    Re-entrant within a process, exclusive between processes.

    Deliberately coarse: it is held across a whole HTTP round trip, which the
    library gives a 15s timeout. Finer granularity is not available to us -- the
    refresh happens inside `_run_request`, and we have no hook into it. Our own
    call sites are the only seam, so they are where the lock goes.
    """
    global _warned_no_flock

    if not _HAVE_FLOCK:
        # No fcntl means no container runtime here, which means the two-process
        # situation this guards against does not arise. Warn once and continue
        # rather than making the server unusable on a platform it may never run
        # two copies on.
        if not _warned_no_flock:
            logger.warning(
                "fcntl.flock unavailable on this platform; cross-process Garmin "
                "locking is disabled. Safe only while a single instance runs."
            )
            _warned_no_flock = True
        yield
        return

    depth = getattr(_local, "depth", 0)
    if depth:
        _local.depth = depth + 1
        try:
            yield
        finally:
            _local.depth -= 1
        return

    path = lock_path_for(tokens_path)
    _local.depth = 1
    try:
        with file_lock(path, timeout):
            yield
    finally:
        _local.depth = 0
