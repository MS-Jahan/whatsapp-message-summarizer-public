from __future__ import annotations
import logging
import httpx
from app.models import Settings

log = logging.getLogger(__name__)


def notify(settings: Settings, text: str, client: httpx.Client | None = None) -> bool:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    owns = client is None
    client = client or httpx.Client(timeout=15.0)
    try:
        r = client.post(url, data={"chat_id": settings.telegram_chat_id, "text": text})
        if r.status_code >= 400:
            log.error("telegram notify failed: %s %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        log.error("telegram notify error: %s", e)
        return False
    finally:
        if owns:
            client.close()
