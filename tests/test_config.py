import pytest
from app.config import load_settings, load_users, ConfigError

BASE_ENV = {
    "GOWA_BASE_URL": "https://gowa.example",
    "GOWA_BASIC_AUTH": "user:pass",
    "GEMINI_API_KEY_FREE": "free",
    "GEMINI_API_KEY_PAID": "paid",
    "MAIL_FROM": "bot@example.com",
    "TELEGRAM_BOT_TOKEN": "tok",
    "TELEGRAM_CHAT_ID": "123",
}


def test_defaults_applied():
    s = load_settings(BASE_ENV)
    assert s.timezone == "Asia/Dhaka"
    assert s.scan_hour == 22
    assert s.gemini_primary_model == "gemini-2.5-flash"
    assert s.gemini_fallback_model == "gemini-2.5-flash-lite"
    assert s.max_video_mb == 10
    assert s.gowa_basic_auth == ("user", "pass")


def test_missing_required_raises():
    env = dict(BASE_ENV)
    del env["GOWA_BASE_URL"]
    with pytest.raises(ConfigError):
        load_settings(env)


def test_load_users_minimal(tmp_path):
    s = load_settings(BASE_ENV)
    p = tmp_path / "users.yaml"
    p.write_text("users:\n  - phone: '8801700000001'\n    mail_to: 'you@example.com'\n")
    users = load_users(str(p), s)
    assert len(users) == 1
    u = users[0]
    assert u.device == "8801700000001@s.whatsapp.net"
    assert u.mail_to == "you@example.com"
    assert u.scan_hour == 22  # inherited default
    assert u.gemini_primary_model == "gemini-2.5-flash"
    assert u.gemini_fallback_model == "gemini-2.5-flash-lite"


def test_load_users_override(tmp_path):
    s = load_settings(BASE_ENV)
    p = tmp_path / "users.yaml"
    p.write_text(
        "users:\n  - phone: '8801700000006'\n    mail_to: 'x@example.com'\n"
        "    scan_hour: 23\n    gemini_primary_model: 'gemini-2.5-pro'\n"
        "    gemini_fallback_model: 'gemini-2.5-flash'\n"
    )
    u = load_users(str(p), s)[0]
    assert u.scan_hour == 23
    assert u.gemini_primary_model == "gemini-2.5-pro"
    assert u.gemini_fallback_model == "gemini-2.5-flash"


def test_load_users_bad_entry_raises(tmp_path):
    s = load_settings(BASE_ENV)
    p = tmp_path / "users.yaml"
    p.write_text("users:\n  - mail_to: 'x@example.com'\n")  # no phone
    with pytest.raises(ConfigError):
        load_users(str(p), s)


def test_email_attachment_defaults():
    s = load_settings(BASE_ENV)
    assert s.max_email_attach_mb == 18
    assert s.max_email_chunks == 5


def test_email_attachment_overrides():
    env = dict(BASE_ENV, MAX_EMAIL_ATTACH_MB="10", MAX_EMAIL_CHUNKS="2")
    s = load_settings(env)
    assert s.max_email_attach_mb == 10
    assert s.max_email_chunks == 2
