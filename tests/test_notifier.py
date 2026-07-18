import httpx
import respx
from app import notifier
from app.models import Settings


def _settings():
    return Settings(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_primary_model="m", gemini_fallback_model="m2",
        gemini_key_free="f", gemini_key_paid="p",
        max_chat_attempts=5, max_video_mb=10, max_media_items=30, max_total_media_mb=40,
        resend_api_key="", smtp_host="", smtp_port=587, smtp_user="", smtp_pass="",
        smtp_tls=True, mail_from="b@x.com", telegram_bot_token="TOK", telegram_chat_id="42",
        log_level="INFO", db_path=":x", users_file="u")


@respx.mock
def test_notify_posts_message():
    route = respx.post("https://api.telegram.org/botTOK/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True}))
    assert notifier.notify(_settings(), "hello") is True
    assert route.called
    sent = route.calls.last.request
    assert b"42" in sent.content and b"hello" in sent.content


@respx.mock
def test_notify_swallows_errors():
    respx.post("https://api.telegram.org/botTOK/sendMessage").mock(
        return_value=httpx.Response(500))
    assert notifier.notify(_settings(), "hello") is False
