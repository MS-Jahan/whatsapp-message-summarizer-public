from __future__ import annotations
import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Sequence
from zoneinfo import ZoneInfo

from app import lock, mailer, notifier
from app.attachments import format_footer, pack_batches
from app.mailer import render_html
from app.config import load_config
from app.gemini import GeminiClient
from app.gowa_client import GowaClient
from app.logging_setup import configure
from app.models import Config, Conversation, QueueRow, Settings, User
from app.names import NameResolver
from app.scanner import day_window, enqueue_today
from app.store import Store
from app.summarizer import summarize

log = logging.getLogger(__name__)


@dataclass
class Deps:
    gowa: object
    gemini: object
    store: Store
    mailer_send: Callable[[str, str, str, Optional[str], Optional[list]], None]
    notify: Callable[[str], None]


def _users_by_device(users: list[User]) -> dict[str, User]:
    return {u.device: u for u in users}


def _display_name(row: QueueRow, resolved: str) -> str:
    """Pick the best conversation name. Prefer the chat's display name from the
    listing when it is a real name; fall back to the resolved group subject /
    contact name; finally the bare jid local part."""
    local = row.chat_jid.split("@", 1)[0]
    placeholders = {"", local, f"Group {local}", f"Newsletter {local}"}
    if row.name and row.name not in placeholders:
        return row.name
    if resolved and resolved != local:
        return resolved
    return row.name or resolved or local


def process_row(row: QueueRow, user: User, deps: Deps, settings: Settings,
                now: datetime) -> bool:
    start, end = day_window(now, settings.timezone)
    try:
        msgs = deps.gowa.get_messages(user.device, row.chat_jid, start, end)
        resolved = deps.gowa.resolve_name(user.device, row.chat_jid)
        name = _display_name(row, resolved)
        conv = Conversation(chat_jid=row.chat_jid, name=name, messages=msgs)
        resolver = NameResolver.from_gowa(deps.gowa, user.device, row.chat_jid)
        summary, media = summarize(conv, deps.gowa, deps.gemini, user, settings, resolver)

        max_bytes = settings.max_email_attach_mb * 1024 * 1024
        batches, oversized = pack_batches(media, max_bytes)
        sendable = batches[:settings.max_email_chunks]
        dropped = [a for batch in batches[settings.max_email_chunks:] for a in batch]

        subject = f"{name} — {row.date}"
        extra_emails = max(0, len(sendable) - 1)
        footer = format_footer(oversized, dropped, extra_emails)
        body = summary + footer
        html = render_html(subject, body)
        first_batch = sendable[0] if sendable else None
        deps.mailer_send(user.mail_to, subject, body, html, first_batch)

        total_parts = len(sendable)
        for i, batch in enumerate(sendable[1:], start=2):
            part_subject = f"{subject} (attachments {i}/{total_parts})"
            part_body = f"Attachments {i}/{total_parts} for {name} — {row.date}"
            deps.mailer_send(user.mail_to, part_subject, part_body, None, batch)

        deps.store.mark_done(row.date, row.device, row.chat_jid)
        return True
    except Exception as e:
        status = deps.store.mark_failed(row.date, row.device, row.chat_jid,
                                        str(e), settings.max_chat_attempts)
        log.exception("conversation failed device=%s chat=%s status=%s",
                      row.device, row.chat_jid, status)
        deps.notify(f"Summary failed for {row.chat_jid} ({status}): {e}")
        return False


def run_once(config: Config, deps: Deps, now: datetime, force: bool = False) -> dict:
    s = config.settings
    by_device = _users_by_device(config.users)
    zone = ZoneInfo(s.timezone)
    enqueued = 0
    start, _ = day_window(now, s.timezone)
    date = start.date().isoformat()

    for user in config.users:
        local_hour = now.astimezone(zone).hour
        due = local_hour >= user.scan_hour and not deps.store.has_scan(date, user.device)
        if force or due:
            try:
                enqueued += enqueue_today(deps.store, deps.gowa, user, s, now)
                if not force:
                    deps.store.mark_scan(date, user.device)
            except Exception as e:
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


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.worker",
        description="Scan WhatsApp conversations and email daily summaries.")
    parser.add_argument(
        "--run-now", "--force", dest="run_now", action="store_true",
        help="Scan and process immediately, ignoring SCAN_HOUR. Idempotent: "
             "already-summarized conversations are not re-sent.")
    args = parser.parse_args(argv)

    configure(os.environ.get("LOG_LEVEL", "INFO"))
    try:
        config = load_config(os.environ)
    except Exception:
        log.exception("config load failed")
        sys.exit(1)
    s = config.settings

    lock_path = os.path.join(os.path.dirname(s.db_path), "worker.lock")
    lock_timeout = int(os.environ.get("LOCK_TIMEOUT_SECONDS", "900"))
    if not lock.acquire(lock_path, lock_timeout):
        sys.exit(0)

    store = Store(s.db_path)
    gowa = GowaClient(s)
    gemini = GeminiClient(s)
    deps = Deps(
        gowa=gowa, gemini=gemini, store=store,
        mailer_send=lambda to, subj, body, html=None, attachments=None:
            mailer.send(s, to, subj, body, html, attachments),
        notify=lambda text: notifier.notify(s, text),
    )
    try:
        if args.run_now:
            log.info("run-now requested: forcing scan regardless of SCAN_HOUR")
        stats = run_once(config, deps, datetime.now().astimezone(), force=args.run_now)
        log.info("run complete: %s", stats)
    except Exception as e:
        log.exception("worker run failed")
        notifier.notify(s, f"Worker crashed: {e}")
        sys.exit(1)
    finally:
        lock.release(lock_path)


if __name__ == "__main__":
    main()
