"""Single-instance enforcement using an exclusive file lock.

Uses fcntl.flock (atomic, kernel-enforced) to prevent TOCTOU races.
The lock is released automatically if the process crashes.
"""

import fcntl
import logging
import os

from sypher_stt.constants import LOCK_FILE

log = logging.getLogger(__name__)


class SingleInstance:
    """Acquires an exclusive flock on the lock file. Returns False if another instance holds it."""

    def __init__(self) -> None:
        self._acquired = False
        self._lock_fd = None

    def acquire(self) -> bool:
        """Try to acquire the single-instance lock.

        Returns:
            True if this is the only instance, False if another is running.
        """
        try:
            fd = os.open(str(LOCK_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            self._lock_fd = os.fdopen(fd, "w")
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd.write(str(os.getpid()))
            self._lock_fd.flush()
            self._acquired = True
            log.debug("Single-instance lock acquired (PID %d).", os.getpid())
            return True
        except OSError:
            log.warning("Another instance is already running.")
            if self._lock_fd:
                self._lock_fd.close()
                self._lock_fd = None
            return False

    def release(self) -> None:
        """Release the lock on shutdown."""
        if self._acquired and self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
                LOCK_FILE.unlink(missing_ok=True)
                log.debug("Single-instance lock released.")
            except OSError as e:
                log.debug("Failed to release lock: %s", e)
            self._lock_fd = None
            self._acquired = False
