# WhatsApp Chat Summarizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a headless Python service that, once per day per WhatsApp account, summarizes each conversation (text + audio + image + small video, multimodal via Gemini) and emails one summary per conversation, run by a Coolify cron every 5 minutes.

**Architecture:** A single idempotent worker (`python -m app.worker`) runs every 5 min. It enqueues today's active conversations once per day into SQLite, then drains the queue: fetch messages from the GoWA REST API, download media, summarize with Gemini (free→paid key failover), and email. Failures stay in the queue and retry on the next tick. Errors go to Telegram. No UI.

**Tech Stack:** Python 3.12, `httpx` (HTTP client), `google-genai` (Gemini), `PyYAML` (users.yaml), `resend` (optional email), stdlib `sqlite3`/`smtplib`/`zoneinfo`/`email`. Tests: `pytest`, `respx` (mock httpx), `freezegun` (mock time).

## Global Constraints

- Python **3.12**.
- WhatsApp access is **only** via the external GoWA REST API (`go-whatsapp-web-multidevice`); never touch WhatsApp directly.
- Device scope value passed to GoWA is the **jid** `<phone>@s.whatsapp.net` (verified accepted; bare phone rejected). No UUID, no `/app/devices` lookup.
- GoWA `messages` endpoint: `limit` **max 100** → always paginate via `offset`. Time filter via `start_time`/`end_time` (RFC3339, inclusive).
- Media download is **two-step**: `GET /message/:id/download?phone=:chat_jid` (returns `results.file_path`) → `GET /<file_path>` for bytes.
- Conversation scope: **1:1 (`@s.whatsapp.net`) and groups (`@g.us`) only**. Exclude `@newsletter` and `status@broadcast`.
- Gemini key failover per summarize call: free key 3 attempts (10s gap) → paid key 3 attempts (10s gap) → raise.
- Video: skip if `file_length > MAX_VIDEO_MB` (default 10) **before download**; a failed/oversized video becomes a text note and must **not** fail the conversation.
- "Today" = calendar day in `TIMEZONE` (default `Asia/Dhaka`); scan enqueues after `SCAN_HOUR` (default 22) local.
- One email per conversation. URLs passed as text, never fetched.
- Secrets/globals in env; per-user routing (`phone` + `mail_to`) in mounted `users.yaml`.
- Logs to stdout. Frequent commits. DRY, YAGNI, TDD.

**Spec reference:** `docs/superpowers/specs/2026-06-24-whatsapp-summarizer-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Project metadata + deps + pytest config |
| `app/__init__.py` | Package marker |
| `app/models.py` | Shared dataclasses: `Message`, `ChatRef`, `Conversation`, `QueueRow`, `Settings`, `User`, `Config` |
| `app/config.py` | Load + validate env (`Settings`) and `users.yaml` (`User`s); build device jid |
| `app/store.py` | SQLite: schema, scan guard, queue enqueue/drain/mark |
| `app/gowa_client.py` | GoWA REST wrapper: chats, messages, two-step media download, name resolution |
| `app/scanner.py` | Select today's in-scope active chats and enqueue |
| `app/gemini.py` | google-genai wrapper with free→paid key failover + retries |
| `app/summarizer.py` | Build multimodal parts (media rules/budgets) + summarize a conversation |
| `app/mailer.py` | Send one email per conversation (Resend if key, else SMTP) |
| `app/notifier.py` | Telegram error notifications |
| `app/logging_setup.py` | Configure stdout structured logging |
| `app/worker.py` | Orchestrate the 5-min run; entry point `python -m app.worker` |
| `tests/...` | One test module per app module |
| `Dockerfile` | Python 3.12 image + ffmpeg + nano + micro |
| `docker-compose.yml` | Service + volumes (users.yaml, db) |
| `users.example.yaml` | Sample config |
| `.env.example` | Sample env |
| `README.md` | Setup, config, Coolify cron instructions |

---

## Task 1: Project scaffolding + data models

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py`
- Create: `app/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: dataclasses used by all later tasks:
  - `Message(id: str, chat_jid: str, sender_jid: str, is_from_me: bool, timestamp: datetime, content: str, media_type: str, filename: str, file_length: int)`
  - `ChatRef(jid: str, name: str, last_message_time: datetime)`
  - `Conversation(chat_jid: str, name: str, messages: list[Message])`
  - `QueueRow(date: str, device: str, chat_jid: str, name: str, status: str, attempts: int)`
  - `User(phone: str, mail_to: str, scan_hour: int, gemini_model: str)` with property `device -> str` returning `f"{phone}@s.whatsapp.net"`
  - `Settings(...)` (all env fields, see Task 2 for full list)
  - `Config(settings: Settings, users: list[User])`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "whatsapp-summarizer"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27",
    "google-genai>=0.3",
    "PyYAML>=6.0",
    "resend>=2.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "respx>=0.21", "freezegun>=1.5"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 2: Create `app/__init__.py`** (empty file)

- [ ] **Step 3: Write the failing test** in `tests/test_models.py`

```python
from datetime import datetime, timezone
from app.models import Message, ChatRef, Conversation, QueueRow, User


def test_user_device_is_jid():
    u = User(phone="8801700000001", mail_to="a@b.com", scan_hour=22, gemini_model="gemini-2.5-flash")
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
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 5: Write `app/models.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Message:
    id: str
    chat_jid: str
    sender_jid: str
    is_from_me: bool
    timestamp: datetime
    content: str
    media_type: str
    filename: str
    file_length: int


@dataclass
class ChatRef:
    jid: str
    name: str
    last_message_time: datetime


@dataclass
class Conversation:
    chat_jid: str
    name: str
    messages: list[Message]


@dataclass
class QueueRow:
    date: str
    device: str
    chat_jid: str
    name: str
    status: str
    attempts: int


@dataclass
class User:
    phone: str
    mail_to: str
    scan_hour: int
    gemini_model: str

    @property
    def device(self) -> str:
        return f"{self.phone}@s.whatsapp.net"


@dataclass
class Settings:
    gowa_base_url: str
    gowa_basic_auth: tuple[str, str]
    timezone: str
    scan_hour: int
    gemini_model: str
    gemini_key_free: str
    gemini_key_paid: str
    max_chat_attempts: int
    max_video_mb: int
    max_media_items: int
    max_total_media_mb: int
    resend_api_key: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    smtp_tls: bool
    mail_from: str
    telegram_bot_token: str
    telegram_chat_id: str
    log_level: str
    db_path: str
    users_file: str


@dataclass
class Config:
    settings: Settings
    users: list[User]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml app/__init__.py app/models.py tests/test_models.py
git commit -m "feat: project scaffolding and shared data models"
```

---

## Task 2: Configuration loading (`config.py`)

**Files:**
- Create: `app/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `Settings`, `User`, `Config` from `app.models`.
- Produces:
  - `load_settings(env: Mapping[str, str]) -> Settings` — reads env with defaults; raises `ConfigError` if a required key is missing.
  - `load_users(path: str, settings: Settings) -> list[User]` — parses `users.yaml`; per-user `scan_hour`/`gemini_model` fall back to settings; raises `ConfigError` on bad shape.
  - `load_config(env, users_path=None) -> Config`.
  - `class ConfigError(Exception)`.
- Required env (raise if missing): `GOWA_BASE_URL`, `GOWA_BASIC_AUTH`, `GEMINI_API_KEY_FREE`, `GEMINI_API_KEY_PAID`, `MAIL_FROM`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- Defaulted env: `TIMEZONE=Asia/Dhaka`, `SCAN_HOUR=22`, `GEMINI_MODEL=gemini-2.5-flash`, `MAX_CHAT_ATTEMPTS=5`, `MAX_VIDEO_MB=10`, `MAX_MEDIA_ITEMS=30`, `MAX_TOTAL_MEDIA_MB=40`, `RESEND_API_KEY=""`, `SMTP_HOST/PORT/USER/PASS=""/587/""/""`, `SMTP_TLS=true`, `LOG_LEVEL=INFO`, `DB_PATH=/data/summarizer.db`, `USERS_FILE=/config/users.yaml`.

- [ ] **Step 1: Write the failing test** in `tests/test_config.py`

```python
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
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.max_video_mb == 10
    assert s.gowa_basic_auth == ("user", "pass")


def test_missing_required_raises():
    env = dict(BASE_ENV); del env["GOWA_BASE_URL"]
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
    assert u.gemini_model == "gemini-2.5-flash"


def test_load_users_override(tmp_path):
    s = load_settings(BASE_ENV)
    p = tmp_path / "users.yaml"
    p.write_text(
        "users:\n  - phone: '8801700000006'\n    mail_to: 'x@example.com'\n"
        "    scan_hour: 23\n    gemini_model: 'gemini-2.5-pro'\n"
    )
    u = load_users(str(p), s)[0]
    assert u.scan_hour == 23 and u.gemini_model == "gemini-2.5-pro"


def test_load_users_bad_entry_raises(tmp_path):
    s = load_settings(BASE_ENV)
    p = tmp_path / "users.yaml"
    p.write_text("users:\n  - mail_to: 'x@example.com'\n")  # no phone
    with pytest.raises(ConfigError):
        load_users(str(p), s)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: Write `app/config.py`**

```python
from __future__ import annotations
from typing import Mapping, Optional
import yaml
from app.models import Settings, User, Config


class ConfigError(Exception):
    pass


_REQUIRED = [
    "GOWA_BASE_URL", "GOWA_BASIC_AUTH", "GEMINI_API_KEY_FREE",
    "GEMINI_API_KEY_PAID", "MAIL_FROM", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
]


def _bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def load_settings(env: Mapping[str, str]) -> Settings:
    missing = [k for k in _REQUIRED if not env.get(k)]
    if missing:
        raise ConfigError(f"missing required env: {', '.join(missing)}")
    auth = env["GOWA_BASIC_AUTH"]
    if ":" not in auth:
        raise ConfigError("GOWA_BASIC_AUTH must be 'user:pass'")
    user, _, pw = auth.partition(":")
    return Settings(
        gowa_base_url=env["GOWA_BASE_URL"].rstrip("/"),
        gowa_basic_auth=(user, pw),
        timezone=env.get("TIMEZONE", "Asia/Dhaka"),
        scan_hour=int(env.get("SCAN_HOUR", "22")),
        gemini_model=env.get("GEMINI_MODEL", "gemini-2.5-flash"),
        gemini_key_free=env["GEMINI_API_KEY_FREE"],
        gemini_key_paid=env["GEMINI_API_KEY_PAID"],
        max_chat_attempts=int(env.get("MAX_CHAT_ATTEMPTS", "5")),
        max_video_mb=int(env.get("MAX_VIDEO_MB", "10")),
        max_media_items=int(env.get("MAX_MEDIA_ITEMS", "30")),
        max_total_media_mb=int(env.get("MAX_TOTAL_MEDIA_MB", "40")),
        resend_api_key=env.get("RESEND_API_KEY", ""),
        smtp_host=env.get("SMTP_HOST", ""),
        smtp_port=int(env.get("SMTP_PORT", "587")),
        smtp_user=env.get("SMTP_USER", ""),
        smtp_pass=env.get("SMTP_PASS", ""),
        smtp_tls=_bool(env.get("SMTP_TLS", "true")),
        mail_from=env["MAIL_FROM"],
        telegram_bot_token=env["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=env["TELEGRAM_CHAT_ID"],
        log_level=env.get("LOG_LEVEL", "INFO"),
        db_path=env.get("DB_PATH", "/data/summarizer.db"),
        users_file=env.get("USERS_FILE", "/config/users.yaml"),
    )


def load_users(path: str, settings: Settings) -> list[User]:
    try:
        with open(path) as f:
            doc = yaml.safe_load(f) or {}
    except FileNotFoundError as e:
        raise ConfigError(f"users file not found: {path}") from e
    entries = doc.get("users")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("users.yaml must contain a non-empty 'users' list")
    users: list[User] = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or not e.get("phone") or not e.get("mail_to"):
            raise ConfigError(f"users[{i}] must have 'phone' and 'mail_to'")
        users.append(User(
            phone=str(e["phone"]).strip(),
            mail_to=str(e["mail_to"]).strip(),
            scan_hour=int(e.get("scan_hour", settings.scan_hour)),
            gemini_model=str(e.get("gemini_model", settings.gemini_model)),
        ))
    return users


def load_config(env: Mapping[str, str], users_path: Optional[str] = None) -> Config:
    settings = load_settings(env)
    users = load_users(users_path or settings.users_file, settings)
    return Config(settings=settings, users=users)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat: env + users.yaml configuration loading"
```

---

## Task 3: State store (`store.py`)

**Files:**
- Create: `app/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `QueueRow` from `app.models`.
- Produces `class Store`:
  - `Store(db_path: str)` — opens connection, creates schema if absent.
  - `has_scan(date: str, device: str) -> bool`
  - `mark_scan(date: str, device: str) -> None`
  - `enqueue(date: str, device: str, chat_jid: str, name: str) -> None` — insert-or-ignore (idempotent on PK).
  - `next_batch(date: str, max_attempts: int, limit: int = 50) -> list[QueueRow]` — rows where `status='pending'` OR (`status='failed'` AND `attempts < max_attempts`).
  - `mark_done(date, device, chat_jid) -> None`
  - `mark_failed(date, device, chat_jid, error: str, max_attempts: int) -> str` — increments `attempts`; sets `status='dead'` if `attempts >= max_attempts` else `'failed'`; returns the new status.
- Schema per spec §9: `daily_scan(date, device_id, status, created_at, PK(date,device_id))`, `chat_queue(date, device_id, chat_jid, name, status, attempts, last_error, updated_at, PK(date,device_id,chat_jid))`.

- [ ] **Step 1: Write the failing test** in `tests/test_store.py`

```python
from app.store import Store


def test_scan_guard(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    assert s.has_scan("2026-06-24", "dev") is False
    s.mark_scan("2026-06-24", "dev")
    assert s.has_scan("2026-06-24", "dev") is True


def test_enqueue_idempotent(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.enqueue("2026-06-24", "dev", "a@s.whatsapp.net", "Alice")
    s.enqueue("2026-06-24", "dev", "a@s.whatsapp.net", "Alice")  # no dup
    rows = s.next_batch("2026-06-24", max_attempts=5)
    assert len(rows) == 1
    assert rows[0].chat_jid == "a@s.whatsapp.net"
    assert rows[0].status == "pending"


def test_mark_done_removes_from_batch(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.enqueue("2026-06-24", "dev", "a", "A")
    s.mark_done("2026-06-24", "dev", "a")
    assert s.next_batch("2026-06-24", max_attempts=5) == []


def test_failed_retries_until_dead(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.enqueue("2026-06-24", "dev", "a", "A")
    assert s.mark_failed("2026-06-24", "dev", "a", "boom", max_attempts=2) == "failed"
    assert len(s.next_batch("2026-06-24", max_attempts=2)) == 1  # still retryable
    assert s.mark_failed("2026-06-24", "dev", "a", "boom", max_attempts=2) == "dead"
    assert s.next_batch("2026-06-24", max_attempts=2) == []  # dead, not retried
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.store'`

- [ ] **Step 3: Write `app/store.py`**

```python
from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timezone
from app.models import QueueRow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_scan (
    date TEXT NOT NULL,
    device_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (date, device_id)
);
CREATE TABLE IF NOT EXISTS chat_queue (
    date TEXT NOT NULL,
    device_id TEXT NOT NULL,
    chat_jid TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (date, device_id, chat_jid)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: str):
        d = os.path.dirname(db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def has_scan(self, date: str, device: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM daily_scan WHERE date=? AND device_id=?", (date, device)
        )
        return cur.fetchone() is not None

    def mark_scan(self, date: str, device: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO daily_scan(date, device_id, status, created_at)"
            " VALUES (?, ?, 'done', ?)", (date, device, _now()))
        self.conn.commit()

    def enqueue(self, date: str, device: str, chat_jid: str, name: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO chat_queue"
            "(date, device_id, chat_jid, name, status, attempts, updated_at)"
            " VALUES (?, ?, ?, ?, 'pending', 0, ?)",
            (date, device, chat_jid, name, _now()))
        self.conn.commit()

    def next_batch(self, date: str, max_attempts: int, limit: int = 50) -> list[QueueRow]:
        cur = self.conn.execute(
            "SELECT date, device_id, chat_jid, name, status, attempts FROM chat_queue"
            " WHERE date=? AND (status='pending' OR (status='failed' AND attempts < ?))"
            " ORDER BY updated_at LIMIT ?", (date, max_attempts, limit))
        return [QueueRow(r["date"], r["device_id"], r["chat_jid"], r["name"],
                         r["status"], r["attempts"]) for r in cur.fetchall()]

    def mark_done(self, date: str, device: str, chat_jid: str) -> None:
        self.conn.execute(
            "UPDATE chat_queue SET status='done', updated_at=?"
            " WHERE date=? AND device_id=? AND chat_jid=?",
            (_now(), date, device, chat_jid))
        self.conn.commit()

    def mark_failed(self, date: str, device: str, chat_jid: str,
                    error: str, max_attempts: int) -> str:
        cur = self.conn.execute(
            "SELECT attempts FROM chat_queue WHERE date=? AND device_id=? AND chat_jid=?",
            (date, device, chat_jid))
        row = cur.fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        status = "dead" if attempts >= max_attempts else "failed"
        self.conn.execute(
            "UPDATE chat_queue SET status=?, attempts=?, last_error=?, updated_at=?"
            " WHERE date=? AND device_id=? AND chat_jid=?",
            (status, attempts, error[:1000], _now(), date, device, chat_jid))
        self.conn.commit()
        return status
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/store.py tests/test_store.py
git commit -m "feat: SQLite state store with scan guard and retry queue"
```

---

## Task 4: GoWA REST client (`gowa_client.py`)

**Files:**
- Create: `app/gowa_client.py`
- Test: `tests/test_gowa_client.py`

**Interfaces:**
- Consumes: `Settings` (for base url + basic auth), `Message`, `ChatRef` from models.
- Produces `class GowaClient`:
  - `GowaClient(settings: Settings, client: httpx.Client | None = None)` — builds an `httpx.Client` with basic auth + base_url if none injected.
  - `list_chats(device: str) -> list[ChatRef]` — paginate `/chats?limit=100&offset=N&device_id=<device>` until `data` empty; parse RFC3339 `last_message_time` to aware datetime.
  - `get_messages(device, chat_jid, since: datetime, until: datetime) -> list[Message]` — paginate `/chat/{chat_jid}/messages?limit=100&offset=N&start_time=&end_time=&device_id=`; parse fields; `start_time`/`end_time` formatted as RFC3339 `Z`.
  - `download_media(device, msg_id, chat_jid) -> tuple[bytes, str]` — step 1 `GET /message/{msg_id}/download?phone={chat_jid}&device_id=` → `results.file_path`; step 2 `GET /{file_path}` → `(content, content_type)`.
  - `resolve_name(device, chat_jid) -> str` — for `@g.us` call `/group/info?group_id=&device_id=` returning group name; for `@s.whatsapp.net` look up `/user/my/contacts?device_id=` and match jid; fall back to the phone/group-id portion of the jid on any failure.
  - `class GowaError(Exception)` — raised on non-2xx or `code != SUCCESS`.

- [ ] **Step 1: Write the failing test** in `tests/test_gowa_client.py`

```python
from datetime import datetime, timezone
import httpx
import respx
from app.config import load_settings
from app.gowa_client import GowaClient

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gowa_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.gowa_client'`

- [ ] **Step 3: Write `app/gowa_client.py`**

```python
from __future__ import annotations
from datetime import datetime
import httpx
from app.models import Settings, Message, ChatRef


class GowaError(Exception):
    pass


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(tz=None).strftime("%Y-%m-%dT%H:%M:%SZ") if dt.tzinfo is None \
        else dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


class GowaClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(
            base_url=settings.gowa_base_url, auth=settings.gowa_basic_auth, timeout=60.0)

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

    def resolve_name(self, device: str, chat_jid: str) -> str:
        local = chat_jid.split("@", 1)[0]
        try:
            if chat_jid.endswith("@g.us"):
                res = self._get("/group/info", {"group_id": chat_jid, "device_id": device})
                return res.get("name") or res.get("subject") or local
            res = self._get("/user/my/contacts", {"device_id": device})
            for c in (res.get("data") or res.get("contacts") or []):
                if c.get("jid") == chat_jid:
                    return c.get("name") or c.get("full_name") or local
        except GowaError:
            return local
        return local
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gowa_client.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/gowa_client.py tests/test_gowa_client.py
git commit -m "feat: GoWA REST client with pagination and two-step media download"
```

---

## Task 5: Scanner (`scanner.py`)

**Files:**
- Create: `app/scanner.py`
- Test: `tests/test_scanner.py`

**Interfaces:**
- Consumes: `GowaClient.list_chats`, `Store.enqueue`, `User`, `Settings`, `ChatRef`.
- Produces:
  - `day_window(now: datetime, tz: str) -> tuple[datetime, datetime]` — start/end (inclusive) of `now`'s calendar day in `tz`, returned as tz-aware datetimes.
  - `in_scope(jid: str) -> bool` — True only for `@s.whatsapp.net` or `@g.us`; False for `@newsletter`, `status@broadcast`, anything else.
  - `enqueue_today(store, gowa, user, settings, now: datetime) -> int` — compute window in `settings.timezone`; list chats; keep in-scope with `last_message_time >= day_start`; enqueue each under `date=day_start.date().isoformat()`; return count enqueued.

- [ ] **Step 1: Write the failing test** in `tests/test_scanner.py`

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from app.scanner import day_window, in_scope, enqueue_today
from app.models import ChatRef, User, Settings
from app.store import Store


def test_in_scope():
    assert in_scope("8801@s.whatsapp.net") is True
    assert in_scope("123@g.us") is True
    assert in_scope("123@newsletter") is False
    assert in_scope("status@broadcast") is False


def test_day_window_dhaka():
    now = datetime(2026, 6, 24, 20, 0, tzinfo=ZoneInfo("Asia/Dhaka"))
    start, end = day_window(now, "Asia/Dhaka")
    assert start.hour == 0 and start.day == 24
    assert end.day == 24 and end.hour == 23


class _FakeGowa:
    def __init__(self, chats): self._chats = chats
    def list_chats(self, device): return self._chats


def _settings():
    return Settings(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_model="m", gemini_key_free="f", gemini_key_paid="p",
        max_chat_attempts=5, max_video_mb=10, max_media_items=30, max_total_media_mb=40,
        resend_api_key="", smtp_host="", smtp_port=587, smtp_user="", smtp_pass="",
        smtp_tls=True, mail_from="b@x.com", telegram_bot_token="t", telegram_chat_id="1",
        log_level="INFO", db_path=":x", users_file="u")


def test_enqueue_today_filters(tmp_path):
    tz = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 6, 24, 22, 30, tzinfo=tz)
    today = datetime(2026, 6, 24, 10, 0, tzinfo=tz)
    old = datetime(2026, 6, 20, 10, 0, tzinfo=tz)
    chats = [
        ChatRef("a@s.whatsapp.net", "Alice", today),     # keep
        ChatRef("g@g.us", "Grp", today),                 # keep
        ChatRef("n@newsletter", "News", today),          # drop (newsletter)
        ChatRef("b@s.whatsapp.net", "Bob", old),         # drop (old)
    ]
    store = Store(str(tmp_path / "t.db"))
    user = User(phone="8801", mail_to="x@y.com", scan_hour=22, gemini_model="m")
    n = enqueue_today(store, _FakeGowa(chats), user, _settings(), now)
    assert n == 2
    rows = store.next_batch("2026-06-24", max_attempts=5)
    assert {r.chat_jid for r in rows} == {"a@s.whatsapp.net", "g@g.us"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scanner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.scanner'`

- [ ] **Step 3: Write `app/scanner.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scanner.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scanner.py tests/test_scanner.py
git commit -m "feat: scanner selects in-scope active chats and enqueues"
```

---

## Task 6: Gemini client with key failover (`gemini.py`)

**Files:**
- Create: `app/gemini.py`
- Test: `tests/test_gemini.py`

**Interfaces:**
- Consumes: `Settings`.
- Produces `class GeminiClient`:
  - `GeminiClient(settings, sleep=time.sleep)` — `sleep` injectable for tests.
  - `generate(parts: list, model: str) -> str` — try free key 3× (10s gap between attempts), then paid key 3× (10s gap), return text on first success; raise `GeminiError` if all fail.
  - `_call(api_key: str, model: str, parts: list) -> str` — single google-genai call; isolated so tests monkeypatch it.
  - `class GeminiError(Exception)`.
- Behavior: 3 attempts per key, `sleep(10)` between attempts (including between the keys). Total max 6 attempts.

- [ ] **Step 1: Write the failing test** in `tests/test_gemini.py`

```python
import pytest
from app.gemini import GeminiClient, GeminiError
from app.models import Settings


def _settings():
    return Settings(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_model="m", gemini_key_free="FREE", gemini_key_paid="PAID",
        max_chat_attempts=5, max_video_mb=10, max_media_items=30, max_total_media_mb=40,
        resend_api_key="", smtp_host="", smtp_port=587, smtp_user="", smtp_pass="",
        smtp_tls=True, mail_from="b@x.com", telegram_bot_token="t", telegram_chat_id="1",
        log_level="INFO", db_path=":x", users_file="u")


def test_free_key_success_first_try():
    g = GeminiClient(_settings(), sleep=lambda s: None)
    calls = []
    g._call = lambda key, model, parts: (calls.append(key) or "summary")
    assert g.generate(["hi"], "m") == "summary"
    assert calls == ["FREE"]


def test_falls_back_to_paid_after_free_fails():
    g = GeminiClient(_settings(), sleep=lambda s: None)
    keys = []
    def fake(key, model, parts):
        keys.append(key)
        if key == "FREE":
            raise RuntimeError("rate limited")
        return "ok"
    g._call = fake
    assert g.generate(["hi"], "m") == "ok"
    assert keys == ["FREE", "FREE", "FREE", "PAID"]  # 3 free then paid succeeds


def test_all_fail_raises():
    g = GeminiClient(_settings(), sleep=lambda s: None)
    g._call = lambda key, model, parts: (_ for _ in ()).throw(RuntimeError("nope"))
    with pytest.raises(GeminiError):
        g.generate(["hi"], "m")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gemini.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.gemini'`

- [ ] **Step 3: Write `app/gemini.py`**

```python
from __future__ import annotations
import logging
import time
from app.models import Settings

log = logging.getLogger(__name__)

ATTEMPTS_PER_KEY = 3
RETRY_GAP_SECONDS = 10


class GeminiError(Exception):
    pass


class GeminiClient:
    def __init__(self, settings: Settings, sleep=time.sleep):
        self.settings = settings
        self.sleep = sleep

    def _call(self, api_key: str, model: str, parts: list) -> str:
        from google import genai
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(model=model, contents=parts)
        return resp.text

    def generate(self, parts: list, model: str) -> str:
        keys = [("free", self.settings.gemini_key_free),
                ("paid", self.settings.gemini_key_paid)]
        last_err: Exception | None = None
        first = True
        for label, key in keys:
            for attempt in range(1, ATTEMPTS_PER_KEY + 1):
                if not first:
                    self.sleep(RETRY_GAP_SECONDS)
                first = False
                try:
                    return self._call(key, model, parts)
                except Exception as e:  # noqa: BLE001 - any provider error retried
                    last_err = e
                    log.warning("gemini %s key attempt %d failed: %s", label, attempt, e)
        raise GeminiError(f"all gemini attempts failed: {last_err}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gemini.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/gemini.py tests/test_gemini.py
git commit -m "feat: Gemini client with free->paid key failover"
```

---

## Task 7: Summarizer (`summarizer.py`)

**Files:**
- Create: `app/summarizer.py`
- Test: `tests/test_summarizer.py`

**Interfaces:**
- Consumes: `Conversation`, `Message`, `User`, `Settings`, `GowaClient.download_media`, `GeminiClient.generate`. Uses `google.genai.types.Part.from_bytes` for media parts (imported lazily).
- Produces:
  - `build_parts(conversation, gowa, user, settings) -> list` — returns a list whose first element is a text prompt+transcript string, followed by media parts. Applies media rules:
    - text → included in transcript with sender label + time.
    - audio, image → download; add as media part (respect `MAX_MEDIA_ITEMS` and `MAX_TOTAL_MEDIA_MB`; overflow → text note).
    - video → if `file_length > MAX_VIDEO_MB*1MB` add note `[video skipped: NN MB > limit]`; else download (on download failure → note `[video could not be processed]`); never raise.
    - document/sticker/call → text note (`[document: name]`, etc.).
    - URL substrings in content are kept as plain text (not fetched).
  - `summarize(conversation, gowa, gemini, user, settings) -> str` — `gemini.generate(build_parts(...), user.gemini_model)`.
- A media-download failure for audio/image is also downgraded to a text note (not raised), so only Gemini/email failures propagate to the queue.

- [ ] **Step 1: Write the failing test** in `tests/test_summarizer.py`

```python
from datetime import datetime, timezone
from app.summarizer import build_parts, _is_text_part
from app.models import Conversation, Message, User, Settings


def _settings(**over):
    base = dict(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_model="m", gemini_key_free="f", gemini_key_paid="p",
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


_USER = User(phone="8801", mail_to="x@y.com", scan_hour=22, gemini_model="m")


def test_text_only_transcript():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(content="hello"), _msg(content="world", is_from_me=True)])
    parts = build_parts(conv, _FakeGowa(), _USER, _settings())
    transcript = parts[0]
    assert "hello" in transcript and "world" in transcript
    assert len(parts) == 1  # no media


def test_large_video_skipped_no_download():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(media_type="video", filename="v.mp4",
                              file_length=20 * 1024 * 1024)])
    g = _FakeGowa()
    parts = build_parts(conv, g, _USER, _settings(max_video_mb=10))
    assert g.downloaded == []  # never downloaded
    assert "video skipped" in parts[0]


def test_image_downloaded_as_part():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(id="img1", media_type="image", filename="p.jpg",
                              file_length=1000)])
    g = _FakeGowa()
    parts = build_parts(conv, g, _USER, _settings())
    assert g.downloaded == ["img1"]
    assert len(parts) == 2  # transcript + one media part


def test_media_budget_overflow_becomes_note():
    msgs = [_msg(id=f"i{i}", media_type="image", filename="p.jpg", file_length=1000)
            for i in range(3)]
    conv = Conversation("a@s.whatsapp.net", "Alice", msgs)
    g = _FakeGowa()
    parts = build_parts(conv, g, _USER, _settings(max_media_items=2))
    assert len(g.downloaded) == 2  # only 2 downloaded
    assert "more media omitted" in parts[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_summarizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.summarizer'`

- [ ] **Step 3: Write `app/summarizer.py`**

```python
from __future__ import annotations
import logging
from app.models import Conversation, User, Settings

log = logging.getLogger(__name__)

_PROMPT = (
    "You are summarizing one WhatsApp conversation from a single day. "
    "Write a concise summary in clear English covering the key points, "
    "decisions, questions, and any action items. Include what is said in any "
    "attached audio and images. Do not invent details. If media was skipped, "
    "note that briefly.\n\n"
    "Conversation '{name}':\n{transcript}\n"
)

_MB = 1024 * 1024


def _is_text_part(part) -> bool:
    return isinstance(part, str)


def _label(msg, name: str) -> str:
    who = "Me" if msg.is_from_me else name
    return f"[{msg.timestamp.isoformat()}] {who}"


def build_parts(conversation: Conversation, gowa, user: User, settings: Settings) -> list:
    lines: list[str] = []
    media_parts: list = []
    notes: list[str] = []
    item_count = 0
    total_bytes = 0
    max_items = settings.max_media_items
    max_total = settings.max_total_media_mb * _MB

    for msg in conversation.messages:
        label = _label(msg, conversation.name)
        mt = msg.media_type
        if mt in ("", "text"):
            if msg.content:
                lines.append(f"{label}: {msg.content}")
            continue
        if mt == "video":
            if msg.file_length > settings.max_video_mb * _MB:
                mb = msg.file_length // _MB
                lines.append(f"{label}: [video skipped: {mb} MB > limit]")
                continue
            ok = _try_add(gowa, user, msg, media_parts, label, lines,
                          item_count, total_bytes, max_items, max_total)
            item_count, total_bytes = ok
            continue
        if mt in ("audio", "image"):
            ok = _try_add(gowa, user, msg, media_parts, label, lines,
                          item_count, total_bytes, max_items, max_total)
            item_count, total_bytes = ok
            continue
        # document / sticker / call / other
        if mt == "call":
            lines.append(f"{label}: [call]")
        else:
            lines.append(f"{label}: [{mt}: {msg.filename or ''}]")

    if notes:
        lines.extend(notes)
    transcript = "\n".join(lines) if lines else "(no text messages)"
    prompt = _PROMPT.format(name=conversation.name, transcript=transcript)
    return [prompt, *media_parts]


def _try_add(gowa, user, msg, media_parts, label, lines,
             item_count, total_bytes, max_items, max_total):
    """Download a media item if within budget; else add a text note. Never raises."""
    if item_count >= max_items or total_bytes + msg.file_length > max_total:
        lines.append(f"{label}: [{msg.media_type} omitted — more media omitted to stay within budget]")
        return item_count, total_bytes
    try:
        data, ctype = gowa.download_media(user.device, msg.id, msg.chat_jid)
    except Exception as e:  # noqa: BLE001
        log.warning("media download failed for %s: %s", msg.id, e)
        if msg.media_type == "video":
            lines.append(f"{label}: [video could not be processed]")
        else:
            lines.append(f"{label}: [{msg.media_type} could not be downloaded]")
        return item_count, total_bytes
    from google.genai import types
    media_parts.append(types.Part.from_bytes(data=data, mime_type=ctype))
    lines.append(f"{label}: [{msg.media_type} attached]")
    return item_count + 1, total_bytes + len(data)


def summarize(conversation: Conversation, gowa, gemini, user: User, settings: Settings) -> str:
    parts = build_parts(conversation, gowa, user, settings)
    return gemini.generate(parts, user.gemini_model)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_summarizer.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/summarizer.py tests/test_summarizer.py
git commit -m "feat: multimodal summarizer with media rules and budgets"
```

---

## Task 8: Mailer (`mailer.py`)

**Files:**
- Create: `app/mailer.py`
- Test: `tests/test_mailer.py`

**Interfaces:**
- Consumes: `Settings`.
- Produces:
  - `send(settings, to: str, subject: str, body: str) -> None` — if `settings.resend_api_key` is set, send via Resend; else send via SMTP using `_send_smtp`.
  - `_send_resend(settings, to, subject, body) -> None` — isolated; tests monkeypatch.
  - `_send_smtp(settings, to, subject, body) -> None` — isolated; tests monkeypatch.
  - `class MailError(Exception)`.

- [ ] **Step 1: Write the failing test** in `tests/test_mailer.py`

```python
import app.mailer as mailer
from app.models import Settings


def _settings(**over):
    base = dict(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_model="m", gemini_key_free="f", gemini_key_paid="p",
        max_chat_attempts=5, max_video_mb=10, max_media_items=30, max_total_media_mb=40,
        resend_api_key="", smtp_host="smtp.x", smtp_port=587, smtp_user="u", smtp_pass="p",
        smtp_tls=True, mail_from="b@x.com", telegram_bot_token="t", telegram_chat_id="1",
        log_level="INFO", db_path=":x", users_file="u")
    base.update(over)
    return Settings(**base)


def test_uses_resend_when_key_present(monkeypatch):
    calls = {}
    monkeypatch.setattr(mailer, "_send_resend",
                        lambda s, to, subj, body: calls.setdefault("resend", (to, subj)))
    monkeypatch.setattr(mailer, "_send_smtp",
                        lambda s, to, subj, body: calls.setdefault("smtp", True))
    mailer.send(_settings(resend_api_key="re_x"), "u@x.com", "Subj", "Body")
    assert "resend" in calls and "smtp" not in calls


def test_uses_smtp_when_no_resend(monkeypatch):
    calls = {}
    monkeypatch.setattr(mailer, "_send_resend", lambda *a: calls.setdefault("resend", True))
    monkeypatch.setattr(mailer, "_send_smtp",
                        lambda s, to, subj, body: calls.setdefault("smtp", (to, subj)))
    mailer.send(_settings(resend_api_key=""), "u@x.com", "Subj", "Body")
    assert "smtp" in calls and "resend" not in calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mailer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.mailer'`

- [ ] **Step 3: Write `app/mailer.py`**

```python
from __future__ import annotations
import smtplib
from email.message import EmailMessage
from app.models import Settings


class MailError(Exception):
    pass


def _send_resend(settings: Settings, to: str, subject: str, body: str) -> None:
    import resend
    resend.api_key = settings.resend_api_key
    resend.Emails.send({
        "from": settings.mail_from, "to": [to],
        "subject": subject, "text": body,
    })


def _send_smtp(settings: Settings, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as srv:
        if settings.smtp_tls:
            srv.starttls()
        if settings.smtp_user:
            srv.login(settings.smtp_user, settings.smtp_pass)
        srv.send_message(msg)


def send(settings: Settings, to: str, subject: str, body: str) -> None:
    try:
        if settings.resend_api_key:
            _send_resend(settings, to, subject, body)
        else:
            _send_smtp(settings, to, subject, body)
    except Exception as e:  # noqa: BLE001
        raise MailError(str(e)) from e
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mailer.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/mailer.py tests/test_mailer.py
git commit -m "feat: mailer with Resend and SMTP backends"
```

---

## Task 9: Telegram notifier (`notifier.py`)

**Files:**
- Create: `app/notifier.py`
- Test: `tests/test_notifier.py`

**Interfaces:**
- Consumes: `Settings`.
- Produces:
  - `notify(settings, text: str, client: httpx.Client | None = None) -> bool` — POST to `https://api.telegram.org/bot<token>/sendMessage` with `chat_id` + `text`; return True on success, False on failure (never raise — notification must not crash the worker).

- [ ] **Step 1: Write the failing test** in `tests/test_notifier.py`

```python
import httpx
import respx
from app import notifier
from app.models import Settings


def _settings():
    return Settings(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_model="m", gemini_key_free="f", gemini_key_paid="p",
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.notifier'`

- [ ] **Step 3: Write `app/notifier.py`**

```python
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
    except Exception as e:  # noqa: BLE001
        log.error("telegram notify error: %s", e)
        return False
    finally:
        if owns:
            client.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_notifier.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/notifier.py tests/test_notifier.py
git commit -m "feat: Telegram error notifier"
```

---

## Task 10: Logging setup (`logging_setup.py`)

**Files:**
- Create: `app/logging_setup.py`
- Test: `tests/test_logging_setup.py`

**Interfaces:**
- Produces: `configure(level: str) -> None` — set root logger to stream to stdout with a structured format (`%(asctime)s %(levelname)s %(name)s %(message)s`) at the given level. Idempotent (clears existing handlers first).

- [ ] **Step 1: Write the failing test** in `tests/test_logging_setup.py`

```python
import logging
from app.logging_setup import configure


def test_configure_sets_level_and_one_handler():
    configure("DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    n = len(root.handlers)
    configure("INFO")  # idempotent: still one handler
    assert len(root.handlers) == n
    assert root.level == logging.INFO
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_logging_setup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.logging_setup'`

- [ ] **Step 3: Write `app/logging_setup.py`**

```python
from __future__ import annotations
import logging
import sys


def configure(level: str) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_logging_setup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/logging_setup.py tests/test_logging_setup.py
git commit -m "feat: stdout logging configuration"
```

---

## Task 11: Worker orchestration (`worker.py`)

**Files:**
- Create: `app/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: everything above. Uses `os.environ` for config and `datetime.now`.
- Produces:
  - `process_row(row, deps, settings, now) -> None` — fetch today's messages for `row.chat_jid`, build `Conversation` (resolve name), `summarizer.summarize`, `mailer.send(to=user.mail_to, subject=f"{name} — {date}", body=summary)`, `store.mark_done`. On exception: `store.mark_failed` + `notifier.notify`; re-raise nothing.
  - `run_once(config, deps, now) -> dict` — for each user: if `now` local hour `>= user.scan_hour` and `not store.has_scan(date, device)`: `enqueue_today` then `store.mark_scan`. Then drain `store.next_batch` and `process_row` each. Return a small stats dict `{"enqueued": int, "processed": int, "failed": int}`.
  - `main() -> None` — `configure` logging, `load_config`, build real deps (`GowaClient`, `GeminiClient`, `Store`), `run_once`, log stats. Top-level try/except → `notifier.notify` + non-zero exit.
  - A `Deps` dataclass bundling `gowa, gemini, store, mailer_send, notify, users_by_device` so tests can inject fakes. (`mailer_send` and `notify` are callables.)

- [ ] **Step 1: Write the failing test** in `tests/test_worker.py`

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from app.worker import run_once, Deps
from app.models import Config, Settings, User, Conversation, Message, ChatRef
from app.store import Store


def _settings():
    return Settings(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_model="m", gemini_key_free="f", gemini_key_paid="p",
        max_chat_attempts=2, max_video_mb=10, max_media_items=30, max_total_media_mb=40,
        resend_api_key="", smtp_host="", smtp_port=587, smtp_user="", smtp_pass="",
        smtp_tls=True, mail_from="b@x.com", telegram_bot_token="t", telegram_chat_id="1",
        log_level="INFO", db_path=":x", users_file="u")


class _FakeGowa:
    def __init__(self, chats, msgs):
        self._chats = chats; self._msgs = msgs
    def list_chats(self, device): return self._chats
    def get_messages(self, device, jid, since, until): return self._msgs.get(jid, [])
    def resolve_name(self, device, jid): return "Alice"
    def download_media(self, *a): return b"", "image/jpeg"


class _FakeGemini:
    def generate(self, parts, model): return "SUMMARY"


def _deps(gowa, store, sent, alerts):
    return Deps(gowa=gowa, gemini=_FakeGemini(), store=store,
                mailer_send=lambda to, subj, body: sent.append((to, subj, body)),
                notify=lambda text: alerts.append(text))


def test_run_once_enqueues_and_emails(tmp_path):
    tz = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 6, 24, 22, 30, tzinfo=tz)
    chats = [ChatRef("a@s.whatsapp.net", "Alice", datetime(2026, 6, 24, 10, tzinfo=tz))]
    msgs = {"a@s.whatsapp.net": [Message("m", "a@s.whatsapp.net", "a@s.whatsapp.net",
            False, datetime(2026, 6, 24, 10, tzinfo=tz), "hi", "", "", 0)]}
    store = Store(str(tmp_path / "t.db"))
    user = User("8801", "x@y.com", 22, "m")
    cfg = Config(settings=_settings(), users=[user])
    sent, alerts = [], []
    deps = _deps(_FakeGowa(chats, msgs), store, sent, alerts)
    stats = run_once(cfg, deps, now)
    assert stats["enqueued"] == 1 and stats["processed"] == 1
    assert sent[0][0] == "x@y.com"
    assert "Alice" in sent[0][1] and "2026-06-24" in sent[0][1]
    assert sent[0][2] == "SUMMARY"
    assert store.next_batch("2026-06-24", max_attempts=2) == []  # done


def test_run_once_before_scan_hour_does_nothing(tmp_path):
    tz = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 6, 24, 9, 0, tzinfo=tz)  # before 22
    store = Store(str(tmp_path / "t.db"))
    cfg = Config(settings=_settings(), users=[User("8801", "x@y.com", 22, "m")])
    sent, alerts = [], []
    deps = _deps(_FakeGowa([], {}), store, sent, alerts)
    stats = run_once(cfg, deps, now)
    assert stats == {"enqueued": 0, "processed": 0, "failed": 0}
    assert sent == []


def test_failed_summary_marks_failed_and_alerts(tmp_path):
    tz = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 6, 24, 22, 30, tzinfo=tz)
    chats = [ChatRef("a@s.whatsapp.net", "Alice", datetime(2026, 6, 24, 10, tzinfo=tz))]
    msgs = {"a@s.whatsapp.net": [Message("m", "a@s.whatsapp.net", "a@s.whatsapp.net",
            False, datetime(2026, 6, 24, 10, tzinfo=tz), "hi", "", "", 0)]}
    store = Store(str(tmp_path / "t.db"))

    class _BoomGemini:
        def generate(self, parts, model): raise RuntimeError("gemini down")

    sent, alerts = [], []
    deps = Deps(gowa=_FakeGowa(chats, msgs), gemini=_BoomGemini(), store=store,
                mailer_send=lambda *a: sent.append(a), notify=lambda t: alerts.append(t))
    cfg = Config(settings=_settings(), users=[User("8801", "x@y.com", 22, "m")])
    stats = run_once(cfg, deps, now)
    assert stats["failed"] == 1 and sent == []
    assert alerts  # telegram alerted
    assert len(store.next_batch("2026-06-24", max_attempts=2)) == 1  # still retryable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.worker'`

- [ ] **Step 3: Write `app/worker.py`**

```python
from __future__ import annotations
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from app import mailer, notifier
from app.config import load_config
from app.gemini import GeminiClient
from app.gowa_client import GowaClient
from app.logging_setup import configure
from app.models import Config, Conversation, QueueRow, Settings, User
from app.scanner import day_window, enqueue_today
from app.store import Store
from app.summarizer import summarize

log = logging.getLogger(__name__)


@dataclass
class Deps:
    gowa: object
    gemini: object
    store: Store
    mailer_send: Callable[[str, str, str], None]
    notify: Callable[[str], None]


def _users_by_device(users: list[User]) -> dict[str, User]:
    return {u.device: u for u in users}


def process_row(row: QueueRow, user: User, deps: Deps, settings: Settings,
                now: datetime) -> bool:
    """Return True on success, False on failure (already recorded)."""
    start, end = day_window(now, settings.timezone)
    try:
        msgs = deps.gowa.get_messages(user.device, row.chat_jid, start, end)
        name = deps.gowa.resolve_name(user.device, row.chat_jid) or row.name
        conv = Conversation(chat_jid=row.chat_jid, name=name, messages=msgs)
        summary = summarize(conv, deps.gowa, deps.gemini, user, settings)
        subject = f"{name} — {row.date}"
        deps.mailer_send(user.mail_to, subject, summary)
        deps.store.mark_done(row.date, row.device, row.chat_jid)
        return True
    except Exception as e:  # noqa: BLE001
        status = deps.store.mark_failed(row.date, row.device, row.chat_jid,
                                        str(e), settings.max_chat_attempts)
        log.exception("conversation failed device=%s chat=%s status=%s",
                      row.device, row.chat_jid, status)
        deps.notify(f"Summary failed for {row.chat_jid} ({status}): {e}")
        return False


def run_once(config: Config, deps: Deps, now: datetime) -> dict:
    s = config.settings
    by_device = _users_by_device(config.users)
    zone = ZoneInfo(s.timezone)
    enqueued = 0
    start, _ = day_window(now, s.timezone)
    date = start.date().isoformat()

    for user in config.users:
        local_hour = now.astimezone(zone).hour
        if local_hour >= user.scan_hour and not deps.store.has_scan(date, user.device):
            try:
                enqueued += enqueue_today(deps.store, deps.gowa, user, s, now)
                deps.store.mark_scan(date, user.device)
            except Exception as e:  # noqa: BLE001
                log.exception("scan failed for %s", user.device)
                deps.notify(f"Scan failed for {user.phone}: {e}")

    processed = 0
    failed = 0
    for row in deps.store.next_batch(date, s.max_chat_attempts):
        user = by_device.get(row.device)
        if user is None:
            continue
        if process_row(row, user, deps, s, now):
            processed += 1
        else:
            failed += 1
    return {"enqueued": enqueued, "processed": processed, "failed": failed}


def main() -> None:
    settings_level = os.environ.get("LOG_LEVEL", "INFO")
    configure(settings_level)
    try:
        config = load_config(os.environ)
    except Exception as e:  # noqa: BLE001
        log.exception("config load failed")
        sys.exit(1)
    s = config.settings
    store = Store(s.db_path)
    gowa = GowaClient(s)
    gemini = GeminiClient(s)
    deps = Deps(
        gowa=gowa, gemini=gemini, store=store,
        mailer_send=lambda to, subj, body: mailer.send(s, to, subj, body),
        notify=lambda text: notifier.notify(s, text),
    )
    try:
        stats = run_once(config, deps, datetime.now().astimezone())
        log.info("run complete: %s", stats)
    except Exception as e:  # noqa: BLE001
        log.exception("worker run failed")
        notifier.notify(s, f"Worker crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_worker.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the whole suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/worker.py tests/test_worker.py
git commit -m "feat: worker orchestration (scan, drain, summarize, email)"
```

---

## Task 12: Packaging — Docker, compose, sample configs, README

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `users.example.yaml`
- Create: `.env.example`
- Create: `README.md`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: `app.worker`. No new app code.
- Produces: deployable image + operator docs. Smoke test asserts the package imports and `main` is callable without running it.

- [ ] **Step 1: Write the failing smoke test** in `tests/test_smoke.py`

```python
import importlib


def test_worker_module_importable_and_has_main():
    mod = importlib.import_module("app.worker")
    assert callable(mod.main)
```

- [ ] **Step 2: Run test to verify it passes** (module already exists)

Run: `pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 3: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nano micro ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app

# Defaults; override in Coolify
ENV DB_PATH=/data/summarizer.db \
    USERS_FILE=/config/users.yaml \
    LOG_LEVEL=INFO

VOLUME ["/data", "/config"]

CMD ["python", "-m", "app.worker"]
```

- [ ] **Step 4: Write `docker-compose.yml`**

```yaml
services:
  summarizer:
    build: .
    image: whatsapp-summarizer:latest
    restart: "no"            # run-to-completion; Coolify cron invokes it
    env_file: .env
    volumes:
      - ./config:/config     # holds users.yaml (edit over SSH / Coolify terminal)
      - summarizer-data:/data # SQLite state, persists across runs

volumes:
  summarizer-data:
```

- [ ] **Step 5: Write `users.example.yaml`**

```yaml
# Copy to ./config/users.yaml. One entry per WhatsApp account to scan.
# 'phone' is the WhatsApp number with country code, no + and no @s.whatsapp.net.
users:
  - phone: "8801700000001"
    mail_to: "you@example.com"

  # Optional per-user overrides:
  # - phone: "8801700000006"
  #   mail_to: "someone@example.com"
  #   scan_hour: 23
  #   gemini_model: "gemini-2.5-pro"
```

- [ ] **Step 6: Write `.env.example`**

```bash
# --- GoWA (go-whatsapp-web-multidevice) ---
GOWA_BASE_URL=https://gowa.example.com
GOWA_BASIC_AUTH=user:pass

# --- Gemini ---
GEMINI_API_KEY_FREE=
GEMINI_API_KEY_PAID=
GEMINI_MODEL=gemini-2.5-flash

# --- Schedule / scope ---
TIMEZONE=Asia/Dhaka
SCAN_HOUR=22
MAX_CHAT_ATTEMPTS=5
MAX_VIDEO_MB=10
MAX_MEDIA_ITEMS=30
MAX_TOTAL_MEDIA_MB=40

# --- Email: Resend OR SMTP ---
RESEND_API_KEY=
MAIL_FROM=bot@example.com
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
SMTP_TLS=true

# --- Telegram error alerts ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# --- Misc ---
LOG_LEVEL=INFO
DB_PATH=/data/summarizer.db
USERS_FILE=/config/users.yaml
```

- [ ] **Step 7: Write `README.md`**

````markdown
# WhatsApp Chat Summarizer

Headless service that emails a daily Gemini summary of each WhatsApp
conversation, pulling messages from a `go-whatsapp-web-multidevice` (GoWA)
instance. Runs as a Coolify cron every 5 minutes.

See the design spec in `docs/superpowers/specs/` for full details.

## Configure

1. `cp .env.example .env` and fill in values (GoWA URL/auth, Gemini keys,
   email, Telegram).
2. `mkdir -p config && cp users.example.yaml config/users.yaml`, then edit
   `config/users.yaml` to list each WhatsApp `phone` + `mail_to`.

## Run locally

```bash
pip install -e ".[dev]"
pytest                 # run tests
python -m app.worker   # one run (enqueue + process)
```

## Deploy on Coolify

1. Deploy this repo as a Docker Compose / Dockerfile app.
2. Set the environment variables from `.env.example` in Coolify.
3. Mount `config/users.yaml` (edit it via SSH or the Coolify terminal —
   `nano`/`micro` are installed in the image). The SQLite DB lives on the
   `summarizer-data` volume.
4. Add a **Scheduled Task** running `python -m app.worker` every 5 minutes
   (`*/5 * * * *`). Runs with nothing pending make no Gemini calls.

## How it works

- Once per day (after `SCAN_HOUR`, in `TIMEZONE`) it enqueues every 1:1 and
  group chat with activity that day (newsletters excluded).
- It then summarizes each queued conversation and emails one summary per
  conversation. Failures are retried on later 5-minute ticks up to
  `MAX_CHAT_ATTEMPTS`, then alerted to Telegram.
````

- [ ] **Step 8: Verify build (optional but recommended)**

Run: `docker build -t whatsapp-summarizer:latest .`
Expected: image builds successfully.

- [ ] **Step 9: Commit**

```bash
git add Dockerfile docker-compose.yml users.example.yaml .env.example README.md tests/test_smoke.py
git commit -m "feat: docker image, compose, sample config, and README"
```

---

## Self-Review

**Spec coverage check (spec §→task):**
- §2 scope (1:1+group, no newsletter) → Task 5 (`in_scope`).
- §3 GoWA endpoints, jid scoping, two-step download, pagination, time filter → Task 4.
- §4 idempotent 5-min worker → Task 11 (`run_once`, scan guard).
- §5 module breakdown → Tasks 2–11 (one per module).
- §6 users.yaml (phone+mail_to, overrides) → Task 2.
- §7 Gemini multimodal + model from config → Tasks 6, 7.
- §7.1 media rules / video skip / budgets → Task 7.
- §8 data flow per conversation → Task 11 (`process_row`).
- §9 SQLite schema + retry semantics → Task 3.
- §10 email Resend/SMTP, subject = name+date → Tasks 8, 11.
- §11 Telegram alerts → Task 9, wired in Task 11.
- §12 stdout logging → Task 10.
- §13 config reference → Task 2.
- §14 Dockerfile/compose/cron, nano+micro → Task 12.
- §15 testing strategy (mocks, sqlite temp, failover, idempotency, windowing) → tests across Tasks 3–11.
- §16 risks: statics-no-auth + disk growth are operator concerns (documented in spec; no code task). Large-transcript truncation is partially addressed by media budgets; full text truncation is **not** implemented — acceptable for v1, noted here as a known limitation.

**Known limitations carried forward (not blockers):**
- No text-transcript truncation for very large chats (spec §16.3). Revisit if prompts exceed model limits.
- Contact-name resolution depends on GoWA `/user/my/contacts` shape; `resolve_name` falls back to the phone/group-id on any mismatch.

**Placeholder scan:** No placeholders, stubs, or "fill this in" markers. Every Step 3 contains complete, runnable code that satisfies its Step 1 tests.

**Type consistency:** `Deps`, `Settings`, `User`, `QueueRow`, `Conversation`, `Message`, `ChatRef` signatures are consistent across tasks. `mailer_send(to, subject, body)` and `notify(text)` callable shapes match between Task 11's `Deps` and its usages.
