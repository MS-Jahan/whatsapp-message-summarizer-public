# WhatsApp Chat Summarizer

> **Note:** Built as an internal office tool (reads configured WhatsApp accounts and emails digests). Publish only with stakeholder permission. Example phone numbers in this repo are **placeholders**, not real accounts.

Headless service that emails a daily [Gemini](https://ai.google.dev/) summary of
each WhatsApp conversation, pulling messages from a
[`go-whatsapp-web-multidevice`](https://github.com/aldinokemal/go-whatsapp-web-multidevice)
(GoWA) instance. It is multimodal (text + audio + images + small video), multi-user,
and runs as a Coolify cron every 5 minutes. No UI.

See the design spec in `docs/superpowers/specs/` for full details.

## How it works

- Once per day (after `SCAN_HOUR`, in `TIMEZONE`) it enqueues every 1:1 and
  group chat with activity that day (newsletters and status broadcasts excluded).
- It then summarizes each queued conversation with Gemini and emails **one
  summary per conversation** to that account's `mail_to`. The subject is
  `<conversation name> — <date>` (group subjects and saved contact names are
  resolved), and the Markdown summary is rendered as HTML (with a plain-text
  fallback) so it displays properly in email clients.
- Each 5-minute tick drains the queue. Failures are retried on later ticks up to
  `MAX_CHAT_ATTEMPTS`, then marked dead and alerted to Telegram. A run with
  nothing pending makes zero Gemini calls.

## Configure

1. `cp .env.example .env` and fill in values (see the reference below).
2. `mkdir -p config && cp users.example.yaml config/users.yaml`, then edit
   `config/users.yaml` to list each WhatsApp `phone` + `mail_to`.

### `users.yaml`

One entry per WhatsApp account to scan. `phone` is the number with country
code, no `+` and no `@s.whatsapp.net` (the device scope is built from it).

```yaml
users:
  - phone: "8801700000001"
    mail_to: "you@example.com"

  # Optional per-user overrides of the global env defaults:
  # - phone: "8801700000006"
  #   mail_to: "someone@example.com"
  #   scan_hour: 23
  #   gemini_primary_model: "gemini-3.1-pro-preview"
  #   gemini_fallback_model: "gemini-3.1-flash-lite"
```

`users.yaml` is re-read on every run, so edits take effect on the next tick — no
restart needed.

### Environment variables

| Var | Required | Default | Purpose |
|---|---|---|---|
| `GOWA_BASE_URL` | yes | — | GoWA REST base URL |
| `GOWA_BASIC_AUTH` | yes | — | `user:pass` for GoWA basic auth |
| `GEMINI_API_KEY_FREE` | yes | — | Gemini key tried first |
| `GEMINI_API_KEY_PAID` | yes | — | Gemini key used as fallback |
| `GEMINI_PRIMARY_MODEL` | no | `gemini-2.5-flash` | Model tried first each round |
| `GEMINI_FALLBACK_MODEL` | no | `gemini-2.5-flash-lite` | Model tried second each round |
| `TIMEZONE` | no | `Asia/Dhaka` | Local timezone for the day window |
| `SCAN_HOUR` | no | `22` | Local hour after which the daily scan runs |
| `MAX_CHAT_ATTEMPTS` | no | `5` | Per-conversation retry cap before "dead" |
| `MAX_VIDEO_MB` | no | `10` | Skip (don't download) videos larger than this |
| `MAX_MEDIA_ITEMS` | no | `30` | Max media items sent to Gemini per conversation |
| `MAX_TOTAL_MEDIA_MB` | no | `40` | Max total media bytes per conversation |
| `MAX_EMAIL_ATTACH_MB` | no | `18` | Max raw attachment bytes per email (Zoho SMTP-safe budget) |
| `MAX_EMAIL_CHUNKS` | no | `5` | Max number of attachment emails per conversation/day; excess items are named, not sent |
| `RESEND_API_KEY` | no | — | If set, send via Resend; otherwise SMTP |
| `MAIL_FROM` | yes | — | Sender address (see SMTP note below) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | no | —/`587`/—/— | SMTP fallback when no Resend key |
| `SMTP_TLS` | no | `true` | Use STARTTLS (ignored on port 465) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | yes | — | Telegram error alerts |
| `LOG_LEVEL` | no | `INFO` | Log verbosity |
| `DB_PATH` | no | `./data/summarizer.db` (local) / `/data/summarizer.db` (Docker) | SQLite state file |
| `USERS_FILE` | no | `./config/users.yaml` (local) / `/config/users.yaml` (Docker) | Path to `users.yaml` |

### Gemini model failover

Each conversation is summarized with one multimodal call, retried in this order
(10s between attempts):

1. **free** key → primary model, then fallback model — repeated **3 rounds**
2. **paid** key → primary model, then fallback model — repeated **3 rounds**

If all 12 attempts fail, the conversation is marked failed and retried on a
later tick. Both models are overridable per user in `users.yaml`.

### Email / SMTP notes

- If `RESEND_API_KEY` is set, Resend is used; otherwise SMTP.
- **Port 465 uses implicit SSL (SMTPS)**; any other port (e.g. `587`) uses
  STARTTLS. The client picks the right mode automatically from `SMTP_PORT`.
- `MAIL_FROM` must be an address the SMTP account is authorized to send as
  (the authenticated mailbox or a verified alias). Many providers (e.g. Zoho)
  reject relaying otherwise with `553 Sender is not allowed to relay`.

## Run locally

The worker reads configuration from the process environment (not from `.env`
directly). Docker Compose injects `.env` via `env_file`; for a bare local run,
export it first:

```bash
pip install -e ".[dev]"
pytest                          # run tests

set -a; . ./.env; set +a        # load .env into the environment

python -m app.worker            # scheduled run: only scans after SCAN_HOUR
python -m app.worker --run-now  # force a scan + process now, ignoring SCAN_HOUR
```

When `DB_PATH` / `USERS_FILE` are unset (the local default), the worker uses the
repo-local `./data/summarizer.db` and `./config/users.yaml` regardless of the
current working directory, so no absolute Docker paths are required. Create your
users file first: `mkdir -p config && cp users.example.yaml config/users.yaml`.

`--run-now` (alias `--force`) is idempotent: conversations already summarized
today are not emailed again, so it is safe to re-run.

## Self-test (GoWA + Gemini + email)

Verify all three integrations end-to-end. This sends a real test email and makes
real Gemini calls, printing one line per check and exiting non-zero on any
failure:

```bash
python -m app.selftest                       # check GoWA, both Gemini models, email
python -m app.selftest --email-to me@x.com   # send the test email elsewhere
python -m app.selftest --skip-email          # connectivity + models only
python -m app.selftest --skip-gemini         # no Gemini calls
```

Example output:

```
[OK  ] gowa[8801700000001] — 167 chats reachable
[OK  ] gemini[primary:gemini-3.5-flash] — free key -> 'OK'
[OK  ] gemini[fallback:gemini-3.1-flash-lite] — free key -> 'OK'
[OK  ] email[you@example.com] — test email sent
ALL CHECKS PASSED
```

## Deploy on Coolify

Use `docker-compose.coolify.yml`. The container stays alive (`sleep infinity`)
so Coolify's Scheduled Task can `docker exec` the worker into it every 5 minutes.

1. Create a new resource → **Docker Compose**, pointed at this repo, and set the
   compose file path to `docker-compose.coolify.yml`.
2. Set the environment variables from `.env.example` in the Coolify UI.
3. Deploy once. Then, in the Coolify terminal, seed your users file:
   ```bash
   cp /config/users.yaml.example /config/users.yaml
   nano /config/users.yaml   # or: micro /config/users.yaml
   ```
   `users.yaml` is re-read every run, so edits apply on the next tick.
4. Add a **Scheduled Task**: command `python -m app.worker`, schedule
   `*/5 * * * *`.
5. To trigger a run immediately, run `python -m app.worker --run-now` in the
   Coolify terminal. To verify GoWA, Gemini, and email, run
   `python -m app.selftest`.

Both `summarizer-config` (users file) and `summarizer-data` (SQLite DB) are
named volumes — they persist across redeploys and Coolify host reboots. The
container seeds `/config/users.yaml.example` from the image on every start
(`cp -n`, so it never overwrites your edited `users.yaml`).
