from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import httpx
import respx
from app.config import load_settings
from app.gowa_client import GowaClient, _rfc3339


def test_rfc3339_emits_utc_z_suffix():
    # GoWA's Go parser requires RFC3339 with 'Z' or '+HH:MM' (colon), never '+0000'.
    aware = datetime(2026, 6, 24, 0, 0, 0, tzinfo=ZoneInfo("Asia/Dhaka"))
    assert _rfc3339(aware) == "2026-06-23T18:00:00Z"  # converted to UTC
    naive = datetime(2026, 6, 24, 12, 30, 0)
    assert _rfc3339(naive) == "2026-06-24T12:30:00Z"  # assumed UTC
    assert "+0000" not in _rfc3339(aware)

ENV = {
    "GOWA_BASE_URL": "https://gowa.test", "GOWA_BASIC_AUTH": "u:p",
    "GEMINI_API_KEY_FREE": "f", "GEMINI_API_KEY_PAID": "p",
    "MAIL_FROM": "b@x.com", "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1",
}


def _client():
    s = load_settings(ENV)
    return GowaClient(s, client=httpx.Client(base_url=s.gowa_base_url, auth=s.gowa_basic_auth))


@respx.mock
def test_list_chats_paginates():
    page1 = {"results": {"data": [
        {"jid": "a@s.whatsapp.net", "name": "Alice", "last_message_time": "2026-06-24T01:00:00Z"}],
        "pagination": {"total": 2}}}
    page2 = {"results": {"data": [
        {"jid": "g@g.us", "name": "Grp", "last_message_time": "2026-06-23T01:00:00Z"}]}}
    empty = {"results": {"data": []}}
    route = respx.get("https://gowa.test/chats")
    route.side_effect = [httpx.Response(200, json=page1),
                         httpx.Response(200, json=page2),
                         httpx.Response(200, json=empty)]
    chats = _client().list_chats("dev@s.whatsapp.net")
    assert [c.jid for c in chats] == ["a@s.whatsapp.net", "g@g.us"]
    assert chats[0].last_message_time == datetime(2026, 6, 24, 1, 0, tzinfo=timezone.utc)


@respx.mock
def test_get_messages_parses_and_paginates():
    msgs = {"results": {"data": [
        {"id": "m1", "chat_jid": "a@s.whatsapp.net", "sender_jid": "a@s.whatsapp.net",
         "is_from_me": False, "timestamp": "2026-06-24T00:40:00Z", "content": "hi",
         "media_type": "", "filename": "", "file_length": 0},
        {"id": "m2", "chat_jid": "a@s.whatsapp.net", "sender_jid": "a@s.whatsapp.net",
         "is_from_me": True, "timestamp": "2026-06-24T00:41:00Z", "content": "",
         "media_type": "audio", "filename": "a.ogg", "file_length": 6631}]}}
    empty = {"results": {"data": []}}
    route = respx.get("https://gowa.test/chat/a@s.whatsapp.net/messages")
    route.side_effect = [httpx.Response(200, json=msgs), httpx.Response(200, json=empty)]
    out = _client().get_messages("dev@s.whatsapp.net", "a@s.whatsapp.net",
                                 datetime(2026, 6, 24, tzinfo=timezone.utc),
                                 datetime(2026, 6, 24, 23, 59, tzinfo=timezone.utc))
    assert len(out) == 2
    assert out[1].media_type == "audio" and out[1].file_length == 6631


@respx.mock
def test_resolve_name_group_uses_capital_name_field():
    respx.get("https://gowa.test/group/info").mock(
        return_value=httpx.Response(200, json={"results": {
            "JID": "120363420800380236@g.us", "Name": "team@vendy.Ltd"}}))
    name = _client().resolve_name("dev@s.whatsapp.net", "120363420800380236@g.us")
    assert name == "team@vendy.Ltd"


@respx.mock
def test_resolve_name_contact_matches_jid_in_list():
    respx.get("https://gowa.test/user/my/contacts").mock(
        return_value=httpx.Response(200, json={"results": [
            {"jid": "8801@s.whatsapp.net", "name": ""},
            {"jid": "8802@s.whatsapp.net", "name": "Mehedi"}]}))
    name = _client().resolve_name("dev@s.whatsapp.net", "8802@s.whatsapp.net")
    assert name == "Mehedi"


@respx.mock
def test_download_media_two_step():
    trigger = {"results": {"file_path": "statics/media/x/y/file.ogg",
                           "file_url": "http://gowa.test/statics/media/x/y/file.ogg"}}
    respx.get("https://gowa.test/message/m2/download").mock(
        return_value=httpx.Response(200, json=trigger))
    respx.get("https://gowa.test/statics/media/x/y/file.ogg").mock(
        return_value=httpx.Response(200, content=b"OGGDATA",
                                    headers={"content-type": "audio/ogg"}))
    data, ctype = _client().download_media("dev@s.whatsapp.net", "m2", "a@s.whatsapp.net")
    assert data == b"OGGDATA" and ctype == "audio/ogg"
