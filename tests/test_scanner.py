from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from app.scanner import day_window, in_scope, enqueue_today
from app.models import ChatRef, User, Settings
from app.store import Store


def test_in_scope():
    assert in_scope("8801@s.whatsapp.net") is True
    assert in_scope("123@g.us") is True
    assert in_scope("123@newsletter") is False
    assert in_scope("status@broadcast") is False


def test_day_window_dhaka():
    now = datetime(2026, 6, 24, 20, 0, tzinfo=ZoneInfo("Asia/Dhaka"))
    start, end = day_window(now, "Asia/Dhaka")
    assert start.hour == 0 and start.day == 24
    assert end.day == 24 and end.hour == 23


class _FakeGowa:
    def __init__(self, chats): self._chats = chats
    def list_chats(self, device): return self._chats


def _settings():
    return Settings(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_primary_model="m", gemini_fallback_model="m2",
        gemini_key_free="f", gemini_key_paid="p",
        max_chat_attempts=5, max_video_mb=10, max_media_items=30, max_total_media_mb=40,
        resend_api_key="", smtp_host="", smtp_port=587, smtp_user="", smtp_pass="",
        smtp_tls=True, mail_from="b@x.com", telegram_bot_token="t", telegram_chat_id="1",
        log_level="INFO", db_path=":x", users_file="u")


def test_enqueue_today_filters(tmp_path):
    tz = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 6, 24, 22, 30, tzinfo=tz)
    today = datetime(2026, 6, 24, 10, 0, tzinfo=tz)
    old = datetime(2026, 6, 20, 10, 0, tzinfo=tz)
    chats = [
        ChatRef("a@s.whatsapp.net", "Alice", today),
        ChatRef("g@g.us", "Grp", today),
        ChatRef("n@newsletter", "News", today),
        ChatRef("b@s.whatsapp.net", "Bob", old),
    ]
    store = Store(str(tmp_path / "t.db"))
    user = User(phone="8801", mail_to="x@y.com", scan_hour=22,
                gemini_primary_model="m", gemini_fallback_model="m2")
    n = enqueue_today(store, _FakeGowa(chats), user, _settings(), now)
    assert n == 2
    rows = store.next_batch("2026-06-24", max_attempts=5)
    assert {r.chat_jid for r in rows} == {"a@s.whatsapp.net", "g@g.us"}
