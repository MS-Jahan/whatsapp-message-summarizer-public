# Coolify-compatible Docker Compose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `docker-compose.coolify.yml` that deploys the worker on Coolify with a Coolify-managed Scheduled Task firing `python -m app.worker` every 5 minutes.

**Architecture:** The worker is run-to-completion (no internal loop). The compose service stays alive via `command: sleep infinity` with `restart: unless-stopped`, so Coolify's Scheduled Task can `docker exec python -m app.worker` into the live container. State persists on a named volume; `users.yaml` rides the existing `./config` bind mount, seeded by a committed `config/users.yaml.example`.

**Tech Stack:** Docker Compose, Coolify, existing Python worker (`app.worker`, `app.selftest`).

## Global Constraints

- Do NOT modify `app/worker.py`, `Dockerfile`, or the existing `docker-compose.yml`.
- Coolify Scheduled Tasks run `docker exec` inside the running container — the container MUST stay alive.
- Worker is headless: no published ports, no healthcheck.
- `DB_PATH=/data/summarizer.db` and `USERS_FILE=/config/users.yaml` are already set by the Dockerfile; do not redefine them in compose.

---

### Task 1: Add `config/users.yaml.example`

Seeds the `config/` directory so it exists in Coolify's git clone (the `./config` bind mount is never empty) and gives the operator a file to copy to `users.yaml`.

**Files:**
- Create: `config/users.yaml.example`

**Interfaces:**
- Consumes: nothing.
- Produces: a tracked file at `/config/users.yaml.example` inside the container at runtime; referenced by Task 2's compose bind mount and Task 3's README copy step.

- [ ] **Step 1: Verify it is not gitignored**

Run: `git check-ignore config/users.yaml.example; echo "exit=$?"`
Expected: no output and `exit=1` (not ignored). The `.gitignore` rule is `/config/users.yaml` (exact), which does not match `users.yaml.example`.

- [ ] **Step 2: Create the file**

```yaml
# Copy to ./config/users.yaml (in the Coolify terminal:
#   cp /config/users.yaml.example /config/users.yaml).
# One entry per WhatsApp account to scan. 'phone' is the WhatsApp number with
# country code, no + and no @s.whatsapp.net.
users:
  - phone: "8801700000001"
    mail_to: "you@example.com"

  # Optional per-user overrides:
  # - phone: "8801700000006"
  #   mail_to: "someone@example.com"
  #   scan_hour: 23
  #   gemini_primary_model: "gemini-2.5-pro"
  #   gemini_fallback_model: "gemini-2.5-flash"
```

- [ ] **Step 3: Confirm git will track it**

Run: `git add config/users.yaml.example && git status --short config/`
Expected: shows `A  config/users.yaml.example`.

- [ ] **Step 4: Commit**

```bash
git add config/users.yaml.example
git commit -m "feat: add config/users.yaml.example for Coolify bind mount"
```

---

### Task 2: Add `docker-compose.coolify.yml`

The Coolify deployment file: a single long-running service Coolify can exec the scheduled task into.

**Files:**
- Create: `docker-compose.coolify.yml`

**Interfaces:**
- Consumes: the `config/users.yaml.example` directory presence from Task 1; the existing `Dockerfile` (`build: .`).
- Produces: a service named `summarizer` that Coolify targets with its Scheduled Task; a named volume `summarizer-data`.

- [ ] **Step 1: Create the file**

```yaml
services:
  summarizer:
    build: .
    image: whatsapp-summarizer:latest
    restart: unless-stopped       # stay alive so Coolify can exec the cron into it
    command: sleep infinity       # PID1 no-op; real work runs via Coolify Scheduled Task
    env_file: .env                # Coolify also injects env vars set in its UI
    volumes:
      - ./config:/config          # holds users.yaml (seeded by users.yaml.example)
      - summarizer-data:/data     # SQLite state, persists across runs/redeploys

volumes:
  summarizer-data:
```

- [ ] **Step 2: Validate the compose file parses**

Run: `docker compose -f docker-compose.coolify.yml config`
Expected: prints the resolved config with `command: sleep infinity` and `restart: unless-stopped`, no errors. (A warning about a missing `.env` file is acceptable.)

- [ ] **Step 3: Verify the container stays alive and accepts an exec'd command**

Run:
```bash
docker compose -f docker-compose.coolify.yml up -d
docker compose -f docker-compose.coolify.yml ps
docker compose -f docker-compose.coolify.yml exec summarizer python -m app.worker --help
docker compose -f docker-compose.coolify.yml down
```
Expected: `ps` shows the service `running` (not exited); `--help` prints the worker usage (proving Coolify's `docker exec` pattern works against the live container). The `--help` path makes no external calls, so it works without real credentials.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.coolify.yml
git commit -m "feat: add Coolify-compatible docker-compose file"
```

---

### Task 3: Update README Coolify section

Document the compose file, the `users.yaml` copy step, the scheduled task, and the bind-mount caveat.

**Files:**
- Modify: `README.md:143-155` (the "## Deploy on Coolify" section)

**Interfaces:**
- Consumes: `docker-compose.coolify.yml` (Task 2) and `config/users.yaml.example` (Task 1).
- Produces: nothing (docs only).

- [ ] **Step 1: Replace the existing Coolify section**

Replace the block from `## Deploy on Coolify` through the end of the file with:

```markdown
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

The SQLite DB lives on the `summarizer-data` volume and persists across
redeploys.

### Caveat: bind-mounted users.yaml

`config/users.yaml` is gitignored, so it is not in the repo Coolify clones — the
bind mount would otherwise be empty. The committed `config/users.yaml.example`
keeps the `config/` directory present so step 3 works. Your edited
`config/users.yaml` lives in the deployment's source directory; if a redeploy
re-clones the repo it can be lost, so re-run step 3 after such a redeploy (or map
the path via Coolify Persistent Storage to make it durable).
```

- [ ] **Step 2: Verify the worker invocation names still exist**

Run: `python -m app.worker --help && python -m app.selftest --help`
Expected: both print usage without error (confirms the commands in the README are valid).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document Coolify compose deployment"
```

---

## Self-Review

**Spec coverage:**
- Compose file with keep-alive + scheduled-task model → Task 2. ✓
- `config/users.yaml.example` deliverable → Task 1. ✓
- README "Deploy on Coolify (compose)" section incl. copy step + caveat → Task 3. ✓
- No change to worker / Dockerfile / existing compose → enforced in Global Constraints. ✓

**Placeholder scan:** No TBD/TODO; all file contents and commands are concrete. ✓

**Type consistency:** Service name `summarizer`, volume `summarizer-data`, paths `/config` and `/data`, and the file name `config/users.yaml.example` are used identically across all three tasks. ✓
