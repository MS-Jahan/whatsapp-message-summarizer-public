from app.store import Store


def test_scan_guard(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    assert s.has_scan("2026-06-24", "dev") is False
    s.mark_scan("2026-06-24", "dev")
    assert s.has_scan("2026-06-24", "dev") is True


def test_enqueue_idempotent(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.enqueue("2026-06-24", "dev", "a@s.whatsapp.net", "Alice")
    s.enqueue("2026-06-24", "dev", "a@s.whatsapp.net", "Alice")  # no dup
    rows = s.next_batch("2026-06-24", max_attempts=5)
    assert len(rows) == 1
    assert rows[0].chat_jid == "a@s.whatsapp.net"
    assert rows[0].status == "pending"


def test_mark_done_removes_from_batch(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.enqueue("2026-06-24", "dev", "a", "A")
    s.mark_done("2026-06-24", "dev", "a")
    assert s.next_batch("2026-06-24", max_attempts=5) == []


def test_failed_retries_until_dead(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.enqueue("2026-06-24", "dev", "a", "A")
    assert s.mark_failed("2026-06-24", "dev", "a", "boom", max_attempts=2) == "failed"
    assert len(s.next_batch("2026-06-24", max_attempts=2)) == 1  # still retryable
    assert s.mark_failed("2026-06-24", "dev", "a", "boom", max_attempts=2) == "dead"
    assert s.next_batch("2026-06-24", max_attempts=2) == []  # dead, not retried
