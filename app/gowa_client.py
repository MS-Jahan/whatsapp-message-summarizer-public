from __future__ import annotations
from datetime import datetime, timezone
import httpx
from app.models import Settings, Message, ChatRef


class GowaError(Exception):
    pass


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _rfc3339(dt: datetime) -> str:
    # GoWA parses with Go's time.RFC3339 (layout ...Z07:00), which rejects a
    # numeric offset without a colon (e.g. "+0000"). Normalize to UTC + "Z".
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GowaClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(
            base_url=settings.gowa_base_url, auth=settings.gowa_basic_auth, timeout=60.0)
        self._contacts_cache: dict[str, dict[str, str]] = {}

    def _get(self, path: str, params: dict) -> dict:
        r = self.client.get(path, params=params)
        if r.status_code >= 400:
            raise GowaError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
        body = r.json()
        if body.get("code") and body["code"] != "SUCCESS":
            raise GowaError(f"GET {path} -> {body.get('code')}: {body.get('message')}")
        return body["results"]

    def list_chats(self, device: str) -> list[ChatRef]:
        out: list[ChatRef] = []
        offset = 0
        while True:
            res = self._get("/chats", {"limit": 100, "offset": offset, "device_id": device})
            data = res.get("data") or []
            if not data:
                break
            for c in data:
                out.append(ChatRef(jid=c["jid"], name=c.get("name", ""),
                                   last_message_time=_parse_dt(c["last_message_time"])))
            offset += len(data)
        return out

    def get_messages(self, device: str, chat_jid: str,
                     since: datetime, until: datetime) -> list[Message]:
        out: list[Message] = []
        offset = 0
        while True:
            res = self._get(f"/chat/{chat_jid}/messages", {
                "limit": 100, "offset": offset, "device_id": device,
                "start_time": _rfc3339(since), "end_time": _rfc3339(until)})
            data = res.get("data") or []
            if not data:
                break
            for m in data:
                out.append(Message(
                    id=m["id"], chat_jid=m.get("chat_jid", chat_jid),
                    sender_jid=m.get("sender_jid", ""), is_from_me=bool(m.get("is_from_me")),
                    timestamp=_parse_dt(m["timestamp"]), content=m.get("content", ""),
                    media_type=m.get("media_type", ""), filename=m.get("filename", ""),
                    file_length=int(m.get("file_length", 0) or 0)))
            offset += len(data)
        return out

    def download_media(self, device: str, msg_id: str, chat_jid: str) -> tuple[bytes, str]:
        res = self._get(f"/message/{msg_id}/download",
                        {"phone": chat_jid, "device_id": device})
        file_path = res["file_path"].lstrip("/")
        r = self.client.get(f"/{file_path}")
        if r.status_code >= 400:
            raise GowaError(f"media fetch {file_path} -> {r.status_code}")
        return r.content, r.headers.get("content-type", "application/octet-stream")

    def contacts_map(self, device: str) -> dict[str, str]:
        """Return ``jid -> saved contact name`` (cached per device for the run)."""
        if device not in self._contacts_cache:
            try:
                res = self._get("/user/my/contacts", {"device_id": device})
            except GowaError:
                self._contacts_cache[device] = {}
                return self._contacts_cache[device]
            contacts = res if isinstance(res, list) else (
                res.get("data") or res.get("contacts") or [])
            self._contacts_cache[device] = {
                c["jid"]: c["name"] for c in contacts
                if c.get("jid") and c.get("name")}
        return self._contacts_cache[device]

    def group_participants(self, device: str, group_jid: str) -> list[dict]:
        res = self._get("/group/info", {"group_id": group_jid, "device_id": device})
        return res.get("Participants") or res.get("participants") or []

    def resolve_name(self, device: str, chat_jid: str) -> str:
        local = chat_jid.split("@", 1)[0]
        try:
            if chat_jid.endswith("@g.us"):
                res = self._get("/group/info", {"group_id": chat_jid, "device_id": device})
                # GoWA returns the group subject in "Name" (capitalized).
                return (res.get("Name") or res.get("name")
                        or res.get("subject") or local)
            return self.contacts_map(device).get(chat_jid) or local
        except GowaError:
            return local
