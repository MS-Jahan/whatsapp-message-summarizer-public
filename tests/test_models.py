from datetime import datetime, timezone
from app.models import Message, ChatRef, Conversation, QueueRow, User, EmailAttachment


def test_user_device_is_jid():
    u = User(phone="8801700000001", mail_to="a@b.com", scan_hour=22,
             gemini_primary_model="gemini-2.5-flash",
             gemini_fallback_model="gemini-2.5-flash-lite")
    assert u.device == "8801700000001@s.whatsapp.net"


def test_message_holds_fields():
    m = Message(
        id="X", chat_jid="j@s.whatsapp.net", sender_jid="j@s.whatsapp.net",
        is_from_me=False, timestamp=datetime(2026, 6, 24, tzinfo=timezone.utc),
        content="hi", media_type="", filename="", file_length=0,
    )
    assert m.content == "hi" and m.media_type == ""


def test_conversation_groups_messages():
    c = Conversation(chat_jid="j", name="Alice", messages=[])
    assert c.name == "Alice" and c.messages == []


def test_email_attachment_fields():
    a = EmailAttachment(filename="x.jpg", mime_type="image/jpeg",
                        data=b"BYTES", label="Alice at 14:32")
    assert a.filename == "x.jpg"
    assert a.mime_type == "image/jpeg"
    assert a.data == b"BYTES"
    assert a.label == "Alice at 14:32"
