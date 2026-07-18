# Coolify-compatible Docker Compose — Design

Date: 2026-06-28

## Problem

The worker (`app.worker`) is **run-to-completion**: `main()` calls `run_once`
once and exits. It has no internal scheduling loop. It therefore needs an
external trigger every 5 minutes.

The current `docker-compose.yml` declares `restart: "no"` with a comment
"Coolify cron invokes it". This is internally inconsistent with how Coolify
actually works: **Coolify Scheduled Tasks run `docker exec` inside the running
application container** (container-scoped, configured in the Coolify UI — not via
compose labels). A `restart: "no"` container exits immediately after its single
run, so there is no live container for the scheduled task to exec into.

References:
- Coolify cron syntax: https://coolify.io/docs/knowledge-base/cron-syntax
- Server-level vs container-level tasks: https://github.com/coollabsio/coolify/issues/8500

## Goal

A new `docker-compose.coolify.yml` that deploys cleanly on Coolify and supports a
Coolify-managed Scheduled Task firing `python -m app.worker` every 5 minutes.
The existing `docker-compose.yml` is left untouched.

## Decisions

- **Scheduling:** Coolify-managed Scheduled Task (chosen by user). Defined in the
  Coolify UI as `python -m app.worker` on `*/5 * * * *`.
- **Keep-alive:** the container must stay running so Coolify can exec into it.
  PID1 = `sleep infinity` via a compose `command:` override.
- **users.yaml:** keep the `./config:/config` bind mount (chosen by user). Add a
  committed `config/users.yaml.example` so the `config/` directory exists in
  Coolify's git clone (the bind mount is no longer empty) and the operator can
  `cp /config/users.yaml.example /config/users.yaml` in the Coolify terminal.
- **File:** new `docker-compose.coolify.yml` (chosen by user); existing
  `docker-compose.yml` untouched.
- **Healthcheck:** none (YAGNI). Coolify treats a running container as healthy;
  the worker already self-alerts to Telegram on failure.

## The compose file

```yaml
services:
  summarizer:
    build: .
    image: whatsapp-summarizer:latest
    restart: unless-stopped       # stay alive so Coolify can exec the cron into it
    command: sleep infinity       # PID1 no-op; real work runs via Coolify Scheduled Task
    env_file: .env                # Coolify also injects env vars; see note below
    volumes:
      - ./config:/config          # holds users.yaml (bind mount; see caveat)
      - summarizer-data:/data     # SQLite state, persists across runs/redeploys

volumes:
  summarizer-data:
```

### Why each line

- `restart: unless-stopped` — replaces the broken `restart: "no"`. Keeps the
  container up across crashes and host reboots so the scheduled task always has a
  target.
- `command: sleep infinity` — overrides the Dockerfile `CMD`
  (`python -m app.worker`, kept as the one-shot default for non-Coolify use).
  The container does nothing on its own; all work is the scheduled `docker exec`.
- `env_file: .env` — kept for local/manual `docker compose` runs. On Coolify,
  environment variables set in the UI are injected into the container
  automatically; the `.env` reference is harmless if absent.
- `summarizer-data` named volume — SQLite DB at `/data/summarizer.db`. The
  Dockerfile already sets `DB_PATH=/data/summarizer.db`.

## Coolify setup (operator steps, documented in README)

1. Deploy this repo as a Docker Compose app, pointing Coolify at
   `docker-compose.coolify.yml`.
2. Set environment variables from `.env.example` in the Coolify UI.
3. Ensure `config/users.yaml` exists on the deployment source path (see caveat).
4. Add a **Scheduled Task**: command `python -m app.worker`, schedule
   `*/5 * * * *`.
5. To trigger immediately: run `python -m app.worker --run-now` in the Coolify
   terminal. To verify integrations: `python -m app.selftest`.

## Caveat: bind-mounted users.yaml + git deploy

`config/users.yaml` is gitignored (`.gitignore` line `/config/users.yaml`), so it
is **not** present in the repository Coolify clones. With the `./config:/config`
bind mount, the container would see an empty `/config` and the worker would fail
to load its users file.

Mitigation (document in README):
- A committed `config/users.yaml.example` guarantees the `config/` directory is
  present in the clone, so the bind mount is never empty. After the first deploy,
  in the Coolify terminal run
  `cp /config/users.yaml.example /config/users.yaml` and edit it (`nano`/`micro`
  are in the image).
- Note that a re-clone on redeploy can wipe an un-persisted file in the source
  dir; using Coolify Persistent Storage for the bind path avoids this. (Switching
  to a named `summarizer-config` volume would also avoid it, but the user chose to
  keep the bind mount.)

## Deliverables

- `docker-compose.coolify.yml` (new).
- `config/users.yaml.example` (new, tracked) — mirrors root `users.example.yaml`.
- README: add a "Deploy on Coolify (compose)" section covering the scheduled
  task, the `users.yaml` copy step, and the bind-mount caveat.

## Out of scope

- No change to `app/worker.py` (no internal scheduler).
- No change to the Dockerfile.
- No change to the existing `docker-compose.yml`.
- No healthcheck, no published ports (worker is headless, no inbound traffic).

## Testing / verification

- `docker compose -f docker-compose.coolify.yml config` — validates the compose
  file parses.
- `docker compose -f docker-compose.coolify.yml up -d` then
  `docker compose -f docker-compose.coolify.yml exec summarizer python -m app.selftest`
  — confirms the keep-alive container accepts an exec'd command (mirrors what
  Coolify's Scheduled Task does).
