from __future__ import annotations
from datetime import datetime, time
from zoneinfo import ZoneInfo
from app.models import User, Settings


def in_scope(jid: str) -> bool:
    return jid.endswith("@s.whatsapp.net") or jid.endswith("@g.us")


def day_window(now: datetime, tz: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    start = datetime.combine(local.date(), time(0, 0, 0), tzinfo=zone)
    end = datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)
    return start, end


def enqueue_today(store, gowa, user: User, settings: Settings, now: datetime) -> int:
    start, _ = day_window(now, settings.timezone)
    date = start.date().isoformat()
    count = 0
    for c in gowa.list_chats(user.device):
        if not in_scope(c.jid):
            continue
        if c.last_message_time < start:
            continue
        store.enqueue(date, user.device, c.jid, c.name)
        count += 1
    return count
