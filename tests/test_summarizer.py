from datetime import datetime, timezone
from app.summarizer import build_parts, _is_text_part, _mime_for
from app.models import Conversation, Message, User, Settings
from app.names import NameResolver


def _resolver(**over):
    base = dict(contacts={}, lid_to_phone={}, display_names={})
    base.update(over)
    return NameResolver(**base)


def _settings(**over):
    base = dict(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_primary_model="m", gemini_fallback_model="m2",
        gemini_key_free="f", gemini_key_paid="p",
        max_chat_attempts=5, max_video_mb=10, max_media_items=30, max_total_media_mb=40,
        resend_api_key="", smtp_host="", smtp_port=587, smtp_user="", smtp_pass="",
        smtp_tls=True, mail_from="b@x.com", telegram_bot_token="t", telegram_chat_id="1",
        log_level="INFO", db_path=":x", users_file="u")
    base.update(over)
    return Settings(**base)


def _msg(**over):
    base = dict(id="m", chat_jid="a@s.whatsapp.net", sender_jid="a@s.whatsapp.net",
                is_from_me=False, timestamp=datetime(2026, 6, 24, tzinfo=timezone.utc),
                content="", media_type="", filename="", file_length=0)
    base.update(over)
    return Message(**base)


class _FakeGowa:
    def __init__(self): self.downloaded = []
    def download_media(self, device, msg_id, chat_jid):
        self.downloaded.append(msg_id)
        return b"BYTES", "image/jpeg"


_USER = User(phone="8801", mail_to="x@y.com", scan_hour=22,
             gemini_primary_model="m", gemini_fallback_model="m2")


def test_mime_normalization():
    # WhatsApp voice notes download as application/ogg; Gemini needs audio/ogg.
    assert _mime_for("audio", "application/ogg") == "audio/ogg"
    assert _mime_for("audio", "audio/ogg; codecs=opus") == "audio/ogg"
    assert _mime_for("image", "image/jpeg") == "image/jpeg"
    assert _mime_for("image", "application/octet-stream") == "image/jpeg"
    assert _mime_for("video", "application/octet-stream") == "video/mp4"


def test_group_labels_each_sender_by_name():
    conv = Conversation("g@g.us", "team@vendy.Ltd", [
        _msg(chat_jid="g@g.us", sender_jid="8801700000002@s.whatsapp.net", content="hi"),
        _msg(chat_jid="g@g.us", sender_jid="8801700000003@s.whatsapp.net", content="yo"),
    ])
    resolver = _resolver(contacts={"8801700000002@s.whatsapp.net": "Aminul"})
    parts, attachments = build_parts(conv, _FakeGowa(), _USER, _settings(), resolver)
    transcript = parts[0]
    assert "Aminul: hi" in transcript                 # resolved contact name
    assert "8801700000003: yo" in transcript          # unknown -> phone fallback


def test_mentions_rewritten_via_lid_and_contacts():
    conv = Conversation("g@g.us", "team@vendy.Ltd", [
        _msg(chat_jid="g@g.us", sender_jid="8801700000002@s.whatsapp.net",
             content="@140557821153343 please check"),
    ])
    resolver = _resolver(
        contacts={"8801700000003@s.whatsapp.net": "Sara"},
        lid_to_phone={"140557821153343@lid": "8801700000003@s.whatsapp.net"})
    parts, attachments = build_parts(conv, _FakeGowa(), _USER, _settings(), resolver)
    assert "@Sara please check" in parts[0]


def test_mention_unknown_falls_back_to_phone():
    conv = Conversation("g@g.us", "Grp", [
        _msg(chat_jid="g@g.us", sender_jid="x@s.whatsapp.net",
             content="@8801700000099 hello"),
    ])
    parts, attachments = build_parts(conv, _FakeGowa(), _USER, _settings(), _resolver())
    assert "@8801700000099 hello" in parts[0]


def test_text_only_transcript():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(content="hello"), _msg(content="world", is_from_me=True)])
    parts, attachments = build_parts(conv, _FakeGowa(), _USER, _settings(), _resolver())
    transcript = parts[0]
    assert "hello" in transcript and "world" in transcript
    assert len(parts) == 1  # no media
    assert attachments == []


def test_large_video_skipped_no_download():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(media_type="video", filename="v.mp4",
                              file_length=20 * 1024 * 1024)])
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(max_video_mb=10), _resolver())
    assert g.downloaded == []  # never downloaded
    assert "video skipped" in parts[0]


def test_image_downloaded_as_part():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(id="img1", media_type="image", filename="p.jpg",
                              file_length=1000)])
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(), _resolver())
    assert g.downloaded == ["img1"]
    assert len(parts) == 2  # transcript + one media part
    assert len(attachments) == 1
    assert attachments[0].mime_type == "image/jpeg"


def test_media_budget_overflow_becomes_note():
    msgs = [_msg(id=f"i{i}", media_type="image", filename="p.jpg", file_length=1000)
            for i in range(3)]
    conv = Conversation("a@s.whatsapp.net", "Alice", msgs)
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(max_media_items=2), _resolver())
    assert len(g.downloaded) == 2  # only 2 downloaded
    assert "more media omitted" in parts[0]


def test_audio_never_collected_as_email_attachment():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(id="a1", media_type="audio", filename="v.ogg",
                              file_length=1000)])
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(), _resolver())
    assert g.downloaded == ["a1"]       # still downloaded for Gemini
    assert attachments == []            # but never collected for email


def test_video_collected_as_email_attachment():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(id="v1", media_type="video", filename="v.mp4",
                              file_length=1000)])
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(max_video_mb=10),
                                     _resolver())
    assert len(attachments) == 1
    # _FakeGowa returns ctype "image/jpeg" regardless of media type; _mime_for
    # coerces non-video ctypes for video messages to "video/mp4" (see
    # test_mime_normalization), so that is the expected normalized MIME here.
    assert attachments[0].mime_type == "video/mp4"


def test_video_skipped_for_gemini_produces_no_attachment():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(media_type="video", filename="v.mp4",
                              file_length=20 * 1024 * 1024)])
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(max_video_mb=10),
                                     _resolver())
    assert attachments == []


def test_budget_omitted_media_produces_no_attachment():
    msgs = [_msg(id=f"i{i}", media_type="image", filename="p.jpg", file_length=1000)
            for i in range(3)]
    conv = Conversation("a@s.whatsapp.net", "Alice", msgs)
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(max_media_items=2),
                                     _resolver())
    assert len(attachments) == 2  # only the 2 actually downloaded


def test_attachment_filename_and_label():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(id="img1", media_type="image", filename="p.jpg",
                              sender_jid="a@s.whatsapp.net",
                              timestamp=datetime(2026, 6, 24, 14, 32, tzinfo=timezone.utc),
                              file_length=1000)])
    g = _FakeGowa()
    resolver = _resolver(contacts={"a@s.whatsapp.net": "Alice"})
    parts, attachments = build_parts(conv, g, _USER, _settings(), resolver)
    a = attachments[0]
    assert a.filename == "143200_image.jpg"
    assert a.label == "Alice at 14:32"


def test_image_collected_as_email_attachment():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(id="img1", media_type="image", filename="p.jpg",
                              file_length=1000)])
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(), _resolver())
    assert len(attachments) == 1
    assert attachments[0].mime_type == "image/jpeg"
    assert b"BYTES" in attachments[0].data


def test_summarize_returns_text_and_attachments_tuple():
    from app.summarizer import summarize
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(id="img1", media_type="image", filename="p.jpg",
                              file_length=1000),
                         _msg(content="hello", media_type="")])
    g = _FakeGowa()
    class FakeGemini:
        def generate(self, parts, primary, fallback):
            return "SUMMARY_TEXT"
    text, attachments = summarize(conv, g, FakeGemini(), _USER, _settings(), _resolver())
    assert isinstance(text, str) and "SUMMARY" in text
    assert isinstance(attachments, list) and len(attachments) == 1
