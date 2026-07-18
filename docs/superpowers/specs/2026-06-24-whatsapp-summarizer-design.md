# WhatsApp Chat Summarizer — Design Spec

**Date:** 2026-06-24
**Status:** Approved design, pending implementation plan

## 1. Purpose

A headless Python service that, once per day, scans each connected WhatsApp
account for conversations that had activity that day, summarizes each
conversation (text + audio + images, multimodal) with the Gemini API, and emails
one summary per conversation. Errors are reported to Telegram. It runs entirely
on a Coolify-hosted cron schedule with no UI.

## 2. Scope

In scope:

- Daily detection of conversations (1:1 and group) with messages "today".
- Multimodal summarization (text, audio, images). URLs are ignored, not fetched.
- One email per conversation per day.
- Conversation types: **1:1 chats (`@s.whatsapp.net`) and group chats (`@g.us`)
  only**. Newsletters/channels (`@newsletter`) and status broadcasts
  (`status@broadcast`) are **excluded**.
- Multi-user: multiple WhatsApp accounts (GoWA devices), each routed to its own
  recipient email.
- Robust retry: API-key failover + per-conversation retry queue.
- Telegram error notifications.
- Structured logging to stdout for Coolify.
- Dockerfile + docker-compose, file-based configuration editable over SSH.

Out of scope:

- Any web/admin UI.
- Managing the WhatsApp session itself (handled by the external GoWA service).
- Fetching / summarizing link contents.
- Replying to or sending WhatsApp messages.

## 3. External dependency: go-whatsapp-web-multidevice (GoWA)

WhatsApp access is provided by an already-running
[go-whatsapp-web-multidevice](https://github.com/aldinokemal/go-whatsapp-web-multidevice)
(GoWA) instance (v8, multi-device). This service owns the WhatsApp Web session
and persists message history in its own DB. Our app is a **pure REST client** of
GoWA — it never touches WhatsApp directly.

Endpoints used:

| Purpose | Method | Path |
|---|---|---|
| List chats | GET | `/chats?limit=&offset=` |
| Get chat messages | GET | `/chat/:chat_jid/messages?start_time=&end_time=&limit=&offset=` |
| Trigger media download | GET | `/message/:message_id/download?phone=:chat_jid` |
| Fetch downloaded bytes | GET | `/:file_path` (under `/statics/...`) |
| Resolve saved contact names | GET | `/user/my/contacts` |
| Resolve group name | GET | `/group/info` |

Auth & device scoping:

- GoWA basic auth via HTTP basic auth (`GOWA_BASIC_AUTH` = `user:pass`).
- Device scoping: each request sends `X-Device-Id: <jid>` or `device_id=<jid>`
  query param, where `<jid>` is the account's WhatsApp jid
  `<phone>@s.whatsapp.net`. **Verified**: GoWA accepts either the device UUID
  *or* the full jid as the scope value. The bare phone number alone is rejected
  (`DEVICE_NOT_FOUND`). So the app builds the scope directly from the
  `users.yaml` phone (`<phone>@s.whatsapp.net`) — **no UUID lookup, no
  `/app/devices` call needed**. With a single device, GoWA also defaults to it.

### Verified against the live instance (2026-06-24)

All of the following were confirmed against the running GoWA instance:

- **`GET /chats`** returns `results.data[]` with `jid`, `name`,
  `last_message_time`, `archived`, plus `results.pagination {limit, offset,
  total}`. → the scanner cheaply selects active chats by `last_message_time >=
  today_start` without reading message bodies.
- **`GET /chat/:jid/messages`** supports `start_time` / `end_time` (RFC3339,
  inclusive) — confirmed it actually filters. **`limit` is capped at 100**, so
  the client must paginate via `offset`. Each message exposes: `id`,
  `chat_jid`, `sender_jid`, `is_from_me`, `timestamp`, `content` (text body),
  `media_type` (`""`=text, `audio`, `image`, `video`, `document`, `sticker`,
  `call`), `filename`, `file_length` (size in **bytes**), and `url` (encrypted
  WA URL — not directly usable).
- **Media download is two-step**:
  1. `GET /message/:id/download?phone=:chat_jid` — `phone` must be the chat jid.
     GoWA downloads + decrypts the media to its own disk and returns
     `results.file_path`, `results.file_url`, `results.file_size`.
  2. `GET /<file_path>` (under `/statics/...`) returns the actual bytes with a
     correct `Content-Type`.
  - Verified payloads: audio → `audio/ogg` (Opus, Gemini-native, no transcode),
    image → `image/jpeg`, video → `video/mp4`.
  - `file_length` from the message list equals the real byte size, so **video
    size limits are enforced before any download** (see §7.1).
  - **Security note:** `/statics/...` is currently served *without*
    authentication. Treat downloaded media paths as public; do not log full
    URLs. (Mitigation tracked in §16.)

## 4. Architecture — single idempotent worker

One Python worker, executed by **Coolify cron every 5 minutes**. No long-running
daemon. Every run is safe to repeat (idempotent). This unifies the "daily scan"
and "retry failures" responsibilities into one code path and satisfies the
requirement that runs with nothing pending consume no Gemini tokens.

```
Coolify cron (*/5 * * * *)
  → python -m app.worker
      1. for each user (device) whose local time >= SCAN_HOUR and has no
         daily_scan row for today: run scan → enqueue one chat_queue row per
         active conversation.
      2. drain chat_queue: rows with status pending or retryable-failed.
      3. per row: fetch today's messages → download media → Gemini multimodal
         summarize → email → mark done.
      4. nothing to do → exit immediately (0 Gemini calls, 0 tokens).
```

### Why this shape

- Idempotent + stateless process = trivially safe to run every 5 min.
- `daily_scan` table guards "enqueue once per day per user".
- Failures stay in the queue and are naturally retried on the next 5-min tick,
  with no separate retry mechanism.

## 5. Components (isolated modules)

Each module has one purpose and a well-defined interface.

| Module | Responsibility | Key interface |
|---|---|---|
| `config.py` | Load + validate env and `users.yaml`. Build each user's device scope as `<phone>@s.whatsapp.net` (no GoWA lookup). | `load_config() -> Config` |
| `gowa_client.py` | Typed wrapper over GoWA REST (chats, messages, two-step media download, contact/group name lookup). Basic auth + device scope (jid) + offset pagination. | `list_chats(device)`, `get_messages(device, jid, since, until)`, `download_media(device, msg_id, chat_jid) -> (bytes, content_type)`, `resolve_name(device, jid) -> str` |
| `scanner.py` | For a user, list chats, keep only `@s.whatsapp.net` (1:1) and `@g.us` (group) with `last_message_time` in today's window, **drop `@newsletter` and `status@broadcast`**, enqueue. | `enqueue_today(user) -> int` |
| `summarizer.py` | Assemble multimodal Gemini request from a conversation's messages (text + audio bytes + image bytes; skip URLs). | `summarize(conversation) -> str` |
| `gemini.py` | `google-genai` wrapper. Free→paid key failover with retries. Model from config. | `generate(parts) -> str` |
| `mailer.py` | Send one email per conversation. Resend if key set, else SMTP. | `send(to, subject, body)` |
| `notifier.py` | Telegram error notifications. | `notify(text)` |
| `store.py` | SQLite access: `daily_scan`, `chat_queue`. | `ensure_scan`, `enqueue`, `next_batch`, `mark_done`, `mark_failed` |
| `worker.py` | Orchestrates the 5-minute run (steps 1–4 above). Entry point `python -m app.worker`. | `main()` |

## 6. Multi-user configuration

Secrets live in environment variables. Per-user routing lives in a mounted
`users.yaml` file (a Docker volume), so the operator can edit it over SSH or the
Coolify terminal **without running any commands in the container** and without
rebuilding. `nano` and `micro` are installed in the image for in-terminal
editing.

Global settings (`timezone`, `scan_hour`, `gemini_model`, all secrets) live in
environment variables. `users.yaml` is intentionally minimal — just **who to
scan and where to mail it**:

`users.yaml`:

```yaml
users:
  - phone: "8801700000001"      # WhatsApp number (no +, no @s.whatsapp.net)
    mail_to: "you@example.com"

  - phone: "8801700000006"
    mail_to: "someone@example.com"
```

- Each entry = one WhatsApp account scanned, routed to one recipient email.
- The device scope is just `<phone>@s.whatsapp.net` — GoWA accepts the jid
  directly, so there is no UUID and no `/app/devices` lookup. The operator only
  ever writes the phone number.
- A `phone` whose jid is not connected in GoWA returns `DEVICE_NOT_FOUND`; that
  user is skipped with a warning and a Telegram alert.
- `users.yaml` is re-read on every worker run, so edits take effect on the next
  5-minute tick — no restart needed.
- (Optional, not required) a user may add `scan_hour` or `gemini_model` to
  override the global env default for that one account.

## 7. Gemini summarization

- One multimodal Gemini call per conversation: a prompt plus inline parts for
  text transcript, audio bytes, and image bytes. URLs are passed as plain text,
  never fetched.
- Model is configurable via `GEMINI_MODEL` env (default `gemini-2.5-flash`),
  optionally overridable per user in `users.yaml`.
- Two API keys: `GEMINI_API_KEY_FREE`, `GEMINI_API_KEY_PAID`.

### Key failover (per summarize call)

1. Free key: up to 3 attempts, 10s between attempts.
2. If all 3 fail → paid key: up to 3 attempts, 10s between attempts.
3. If all paid attempts fail → raise → conversation marked `failed` in queue.

This is the **API-level** retry layer (within a single worker run). It is
distinct from the **conversation-level** retry layer (the queue, across runs).

### 7.1 Media handling rules

- **Audio**: downloaded and passed to Gemini as-is (Ogg/Opus is Gemini-native).
- **Image**: downloaded and passed to Gemini as-is (JPEG/PNG).
- **Video**: gated by size and best-effort:
  - If `file_length > MAX_VIDEO_MB` (default **10 MB**), **skip the video** — do
    not download it. A short note (`[video skipped: NN MB > limit]`) is added to
    the conversation context so the summary acknowledges it.
  - Otherwise download and include it. If the download **or** Gemini processing
    of that video fails, **skip just that video** (note `[video could not be
    processed]`) and continue. A skipped/failed video must **not** fail the
    whole conversation.
- **document / sticker / call**: not sent to Gemini. Represented as short text
  notes (e.g. `[document: filename]`, `[call]`) so the summary stays accurate.
- **URLs**: passed as plain text only; never fetched.
- An overall per-conversation media budget (`MAX_MEDIA_ITEMS`,
  `MAX_TOTAL_MEDIA_MB`) bounds Gemini cost; excess media beyond the budget is
  represented as text notes.

## 8. Data flow per conversation

1. `get_messages(device_id, jid, since=today_start, until=today_end)`, paginating
   by `offset` (limit 100) until the window is exhausted.
2. Partition messages by `media_type`: text bodies; audio + image message-ids
   (always eligible); video message-ids **only if `file_length <=
   MAX_VIDEO_MB`**; everything else (document/sticker/call/URL) → text notes.
3. For each eligible media id: `download_media` (two-step) → bytes. A failed
   media download is converted to a text note and does **not** abort the
   conversation (video especially — see §7.1).
4. Build multimodal request → `summarizer.summarize` → `gemini.generate`.
5. `mailer.send(to=user.mail_to, subject="<chat name> — <date>", body=summary)`.
6. `store.mark_done`.

A media-level failure (download or per-item Gemini issue) is downgraded to a
text note and logged. A conversation-level exception in steps 1–5 (e.g. all
Gemini keys exhausted, email send fails) → `store.mark_failed` (increment
attempts, record error) + `notifier.notify`. One conversation never blocks
others.

## 9. State — SQLite

`DB_PATH` points at a file on a mounted volume (persists across deploys).

`daily_scan`:

| column | type | notes |
|---|---|---|
| date | TEXT | local date `YYYY-MM-DD` |
| device_id | TEXT | |
| status | TEXT | `done` |
| created_at | TEXT | |
| | | PK (date, device_id) |

`chat_queue`:

| column | type | notes |
|---|---|---|
| date | TEXT | local date |
| device_id | TEXT | |
| chat_jid | TEXT | |
| name | TEXT | contact / group display name |
| status | TEXT | `pending` \| `done` \| `failed` \| `dead` |
| attempts | INTEGER | conversation-level attempt count |
| last_error | TEXT | |
| updated_at | TEXT | |
| | | PK (date, device_id, chat_jid) |

Retry semantics:

- `pending` and `failed` (with `attempts < MAX_CHAT_ATTEMPTS`) are picked up on
  each run.
- On reaching `MAX_CHAT_ATTEMPTS`, status becomes `dead`, no longer retried, and
  a Telegram alert is sent.

## 10. Email delivery

- One email per conversation that had activity (per the requirement).
- Subject uses the resolved conversation name: saved contact name (via
  `/user/my/contacts`) for 1:1, group subject (via `/group/info`) for groups,
  falling back to the phone number / group id when no name is available. (Live
  check showed most chats expose only the phone number until resolved.)
- Provider selection: if `RESEND_API_KEY` is set → Resend; otherwise SMTP via
  `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_TLS`.
- `MAIL_FROM` is global; `mail_to` is per user from `users.yaml`.
- No Gmail OAuth needed — SMTP credentials only.

## 11. Notifications — Telegram

- Any error (per-conversation failure, scan failure, config error, GoWA
  unreachable) sends a message via the Telegram Bot API.
- Config: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## 12. Logging

- Structured logs to **stdout** (Coolify captures container logs).
- `LOG_LEVEL` env (default `INFO`). Each line includes run id, device_id, and
  chat_jid where relevant for traceability.

## 13. Configuration reference

Environment variables (secrets + infra):

| Var | Purpose |
|---|---|
| `GOWA_BASE_URL` | GoWA REST base URL |
| `GOWA_BASIC_AUTH` | `user:pass` for GoWA basic auth |
| `TIMEZONE` | Local timezone for the day window (default `Asia/Dhaka`) |
| `SCAN_HOUR` | Hour (local) after which the daily scan enqueues (default 22) |
| `GEMINI_MODEL` | Gemini model id (default `gemini-2.5-flash`) |
| `GEMINI_API_KEY_FREE` | Free Gemini key (tried first) |
| `GEMINI_API_KEY_PAID` | Paid Gemini key (fallback) |
| `MAX_CHAT_ATTEMPTS` | Conversation-level retry cap (default 5) |
| `MAX_VIDEO_MB` | Skip videos larger than this; no download (default 10) |
| `MAX_MEDIA_ITEMS` | Max media items sent to Gemini per conversation (default 30) |
| `MAX_TOTAL_MEDIA_MB` | Max total media bytes sent to Gemini per conversation (default 40) |
| `RESEND_API_KEY` | If set, use Resend; else SMTP |
| `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASS`/`SMTP_TLS` | SMTP fallback |
| `MAIL_FROM` | Sender address |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat id for alerts |
| `LOG_LEVEL` | Log verbosity (default INFO) |
| `DB_PATH` | SQLite file path (on volume) |
| `USERS_FILE` | Path to `users.yaml` (default `/config/users.yaml`) |

File config (`users.yaml`): a list of `{phone, mail_to}` entries — one per
WhatsApp account to scan. Optional per-user `scan_hour` / `gemini_model`
overrides. See §6.

## 14. Deployment

- **Dockerfile**: Python 3.12 slim base; installs `ffmpeg` if needed for audio
  handling, plus `nano` and `micro`; copies app; entrypoint `python -m
  app.worker`.
- **docker-compose.yml**: the summarizer service + volumes for `users.yaml`,
  SQLite DB, and (optionally) media temp. Env via Coolify.
- **Cron**: Coolify scheduled task runs the container/command every 5 minutes.
- **Git**: repository initialized; changes committed.

## 15. Testing strategy

- Unit tests with GoWA, Gemini, mailer, and Telegram all mocked.
- `store.py`: real SQLite against a temp file (fast, deterministic).
- Key failover: simulate free-key failures, assert paid-key fallback and the
  3×/10s retry pattern (with time mocked).
- Idempotency: two consecutive worker runs over the same day enqueue once and
  don't re-email `done` conversations.
- Scanner windowing: messages just inside/outside the local-day boundary in
  `Asia/Dhaka`.

## 16. Open questions / risks

Resolved by live verification (2026-06-24):

- ~~GoWA message API shape~~ — **confirmed**: time filter, offset pagination
  (limit ≤ 100), and all needed fields present (§3).
- ~~Audio format~~ — **confirmed** Ogg/Opus, Gemini-native, no transcode (§3).
- ~~Media download mechanism~~ — **confirmed** two-step (trigger → fetch
  `/statics`) (§3).
- ~~Media cost bounds~~ — addressed via `MAX_VIDEO_MB`, `MAX_MEDIA_ITEMS`,
  `MAX_TOTAL_MEDIA_MB` (§7.1, §13).

Remaining / to handle in implementation:

1. **Unauthenticated `/statics`**: GoWA serves downloaded media without auth.
   Risk that decrypted media is publicly reachable by anyone who knows the path.
   Mitigations to confirm with the operator: rely on path unguessability +
   periodic cleanup of GoWA's `statics/media`, restrict the GoWA host, or fetch
   bytes only over the trusted network. Do not log full media URLs/paths.
2. **GoWA disk growth**: every download persists a file on the GoWA server.
   Implementation should not assume cleanup; flag for an operator-side retention
   policy (out of scope for this app, but noted).
3. **Large daily conversations**: even with media budgets, a chat with hundreds
   of text messages may produce a large prompt. Implementation should chunk or
   truncate transcripts sensibly and note truncation in the summary.
