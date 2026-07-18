import pytest
from app.gemini import GeminiClient, GeminiError
from app.models import Settings


def _settings():
    return Settings(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_primary_model="PRI", gemini_fallback_model="FB",
        gemini_key_free="FREE", gemini_key_paid="PAID",
        max_chat_attempts=5, max_video_mb=10, max_media_items=30, max_total_media_mb=40,
        resend_api_key="", smtp_host="", smtp_port=587, smtp_user="", smtp_pass="",
        smtp_tls=True, mail_from="b@x.com", telegram_bot_token="t", telegram_chat_id="1",
        log_level="INFO", db_path=":x", users_file="u")


def test_free_primary_success_first_try():
    g = GeminiClient(_settings(), sleep=lambda s: None)
    calls = []
    g._call = lambda key, model, parts: (calls.append((key, model)) or "summary")
    assert g.generate(["hi"], "PRI", "FB") == "summary"
    assert calls == [("FREE", "PRI")]


def test_free_fallback_used_after_primary_fails():
    g = GeminiClient(_settings(), sleep=lambda s: None)
    calls = []

    def fake(key, model, parts):
        calls.append((key, model))
        if model == "PRI":
            raise RuntimeError("primary down")
        return "ok"

    g._call = fake
    assert g.generate(["hi"], "PRI", "FB") == "ok"
    # free primary fails, then free fallback succeeds within the first round
    assert calls == [("FREE", "PRI"), ("FREE", "FB")]


def test_full_retry_order_then_raises():
    g = GeminiClient(_settings(), sleep=lambda s: None)
    calls = []

    def fake(key, model, parts):
        calls.append((key, model))
        raise RuntimeError("nope")

    g._call = fake
    with pytest.raises(GeminiError):
        g.generate(["hi"], "PRI", "FB")
    # free [primary, fallback] x3, then paid [primary, fallback] x3 = 12 attempts
    expected = (
        [("FREE", "PRI"), ("FREE", "FB")] * 3
        + [("PAID", "PRI"), ("PAID", "FB")] * 3
    )
    assert calls == expected


def test_falls_back_to_paid_after_all_free_attempts_fail():
    g = GeminiClient(_settings(), sleep=lambda s: None)
    calls = []

    def fake(key, model, parts):
        calls.append((key, model))
        if key == "FREE":
            raise RuntimeError("free exhausted")
        return "paid-ok"

    g._call = fake
    assert g.generate(["hi"], "PRI", "FB") == "paid-ok"
    # all 6 free attempts fail, then first paid (primary) succeeds
    assert calls == [("FREE", "PRI"), ("FREE", "FB")] * 3 + [("PAID", "PRI")]


def test_sleep_between_every_attempt_but_not_first():
    sleeps = []
    g = GeminiClient(_settings(), sleep=lambda s: sleeps.append(s))

    def fake(key, model, parts):
        raise RuntimeError("nope")

    g._call = fake
    with pytest.raises(GeminiError):
        g.generate(["hi"], "PRI", "FB")
    # 12 attempts → 11 gaps of 10s, none before the first attempt
    assert sleeps == [10] * 11
