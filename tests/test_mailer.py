import smtplib

import app.mailer as mailer
from app.models import EmailAttachment, Settings


class _FakeSMTP:
    """Records construction + method calls for SMTP / SMTP_SSL."""
    instances = []

    def __init__(self, host, port, timeout=30):
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in = None
        self.sent = False
        self.sent_msg = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, pw):
        self.logged_in = user

    def send_message(self, msg):
        self.sent = True
        self.sent_msg = msg


def _settings(**over):
    base = dict(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_primary_model="m", gemini_fallback_model="m2",
        gemini_key_free="f", gemini_key_paid="p",
        max_chat_attempts=5, max_video_mb=10, max_media_items=30, max_total_media_mb=40,
        resend_api_key="", smtp_host="smtp.x", smtp_port=587, smtp_user="u", smtp_pass="p",
        smtp_tls=True, mail_from="b@x.com", telegram_bot_token="t", telegram_chat_id="1",
        log_level="INFO", db_path=":x", users_file="u")
    base.update(over)
    return Settings(**base)


def test_uses_resend_when_key_present(monkeypatch):
    calls = {}
    monkeypatch.setattr(mailer, "_send_resend",
                        lambda s, to, subj, body, html=None, attachments=None: calls.setdefault("resend", (to, subj, html)))
    monkeypatch.setattr(mailer, "_send_smtp",
                        lambda s, to, subj, body, html=None, attachments=None: calls.setdefault("smtp", True))
    mailer.send(_settings(resend_api_key="re_x"), "u@x.com", "Subj", "Body", "<p>Body</p>")
    assert "resend" in calls and "smtp" not in calls
    assert calls["resend"][2] == "<p>Body</p>"


def test_uses_smtp_when_no_resend(monkeypatch):
    calls = {}
    monkeypatch.setattr(mailer, "_send_resend", lambda *a, **k: calls.setdefault("resend", True))
    monkeypatch.setattr(mailer, "_send_smtp",
                        lambda s, to, subj, body, html=None, attachments=None: calls.setdefault("smtp", (to, subj)))
    mailer.send(_settings(resend_api_key=""), "u@x.com", "Subj", "Body")
    assert "smtp" in calls and "resend" not in calls


def test_render_html_converts_markdown_and_escapes_title():
    html = mailer.render_html("Alice & <Bob>", "# Hi\n\n- one\n- two\n\n**bold**")
    assert "<li>one</li>" in html and "<li>two</li>" in html
    assert "<strong>bold</strong>" in html
    assert "Alice &amp; &lt;Bob&gt;" in html  # title escaped
    assert html.lstrip().startswith("<!doctype html>")


def test_smtp_attaches_html_alternative(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    mailer._send_smtp(_settings(smtp_port=587), "to@x.com", "Subj", "plain",
                      html="<p>rich</p>")
    srv = _FakeSMTP.instances[-1]
    assert srv.sent_msg.is_multipart()
    types = {p.get_content_type() for p in srv.sent_msg.walk()}
    assert "text/plain" in types and "text/html" in types


def test_port_465_uses_implicit_ssl(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    mailer._send_smtp(_settings(smtp_port=465, smtp_user="u@x.com", smtp_pass="pw"),
                      "to@x.com", "Subj", "Body")
    srv = _FakeSMTP.instances[-1]
    assert srv.port == 465
    assert srv.started_tls is False  # SMTP_SSL never calls starttls
    assert srv.logged_in == "u@x.com" and srv.sent is True


def test_port_587_uses_starttls(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    mailer._send_smtp(_settings(smtp_port=587, smtp_tls=True, smtp_user="u@x.com",
                                smtp_pass="pw"),
                      "to@x.com", "Subj", "Body")
    srv = _FakeSMTP.instances[-1]
    assert srv.port == 587
    assert srv.started_tls is True
    assert srv.sent is True


def test_smtp_adds_attachments(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    atts = [EmailAttachment(filename="p.jpg", mime_type="image/jpeg",
                            data=b"BYTES", label="Alice at 14:32")]
    mailer._send_smtp(_settings(smtp_port=587), "to@x.com", "Subj", "Body",
                      attachments=atts)
    srv = _FakeSMTP.instances[-1]
    found = [p for p in srv.sent_msg.walk() if p.get_content_type() == "image/jpeg"]
    assert len(found) == 1
    assert found[0].get_filename() == "p.jpg"
    assert found[0].get_payload(decode=True) == b"BYTES"


def test_smtp_no_attachments_param_is_backward_compatible(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    mailer._send_smtp(_settings(smtp_port=587), "to@x.com", "Subj", "Body")
    srv = _FakeSMTP.instances[-1]
    assert srv.sent is True


def test_resend_includes_base64_attachments(monkeypatch):
    import sys
    import types as _types
    fake_resend = _types.ModuleType("resend")
    fake_resend.api_key = None
    sent_payload = {}

    class _Emails:
        @staticmethod
        def send(payload):
            sent_payload.update(payload)
    fake_resend.Emails = _Emails
    monkeypatch.setitem(sys.modules, "resend", fake_resend)

    atts = [EmailAttachment(filename="p.jpg", mime_type="image/jpeg",
                            data=b"BYTES", label="Alice at 14:32")]
    mailer._send_resend(_settings(resend_api_key="re_x"), "to@x.com", "Subj",
                        "Body", attachments=atts)
    assert "attachments" in sent_payload
    assert sent_payload["attachments"][0]["filename"] == "p.jpg"
    import base64
    assert base64.b64decode(sent_payload["attachments"][0]["content"]) == b"BYTES"


def test_send_passes_attachments_through_to_smtp(monkeypatch):
    calls = {}
    monkeypatch.setattr(mailer, "_send_resend",
                        lambda s, to, subj, body, html=None, attachments=None:
                            calls.setdefault("resend", True))
    monkeypatch.setattr(mailer, "_send_smtp",
                        lambda s, to, subj, body, html=None, attachments=None:
                            calls.setdefault("smtp_atts", attachments))
    atts = [EmailAttachment(filename="p.jpg", mime_type="image/jpeg",
                            data=b"X", label="L")]
    mailer.send(_settings(resend_api_key=""), "u@x.com", "Subj", "Body",
               attachments=atts)
    assert calls["smtp_atts"] == atts
