from __future__ import annotations
import logging
import os
import time

log = logging.getLogger(__name__)


def acquire(path: str, timeout_seconds: int) -> bool:
    """Atomically acquire a lock file. Returns False if another instance holds it."""
    if os.path.exists(path):
        try:
            with open(path) as f:
                ts = float(f.read().strip())
            age = time.time() - ts
            if age < timeout_seconds:
                log.warning("lock held by another process (age=%.0fs), exiting", age)
                return False
            log.warning("stale lock (age=%.0fs > %ds), removing", age, timeout_seconds)
            os.remove(path)
        except (ValueError, OSError):
            try:
                os.remove(path)
            except OSError:
                pass

    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(str(time.time()))
        return True
    except FileExistsError:
        log.warning("lock acquired by concurrent process, exiting")
        return False


def release(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
