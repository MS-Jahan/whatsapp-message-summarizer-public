import time
from app.lock import acquire, release


def test_acquire_creates_lock_file(tmp_path):
    p = str(tmp_path / "worker.lock")
    assert acquire(p, 900) is True
    assert open(p).read().strip()  # contains a timestamp


def test_second_acquire_fails_while_held(tmp_path):
    p = str(tmp_path / "worker.lock")
    acquire(p, 900)
    assert acquire(p, 900) is False


def test_stale_lock_is_overridden(tmp_path):
    p = str(tmp_path / "worker.lock")
    # write a lock timestamp 20 minutes in the past
    with open(p, "w") as f:
        f.write(str(time.time() - 1200))
    assert acquire(p, 900) is True  # stale → removed and re-acquired


def test_release_removes_file(tmp_path):
    p = str(tmp_path / "worker.lock")
    acquire(p, 900)
    release(p)
    assert not __import__("os").path.exists(p)


def test_release_is_safe_when_no_lock(tmp_path):
    p = str(tmp_path / "worker.lock")
    release(p)  # must not raise


def test_acquire_after_release(tmp_path):
    p = str(tmp_path / "worker.lock")
    acquire(p, 900)
    release(p)
    assert acquire(p, 900) is True
