from __future__ import annotations
import argparse
import logging
import os
import sys
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from app import mailer
from app.config import load_config
from app.gemini import GeminiClient
from app.gowa_client import GowaClient
from app.logging_setup import configure
from app.models import Config, Settings

log = logging.getLogger(__name__)

_GEMINI_PROBE = "Reply with exactly the word: OK"
_EMAIL_SUBJECT = "WhatsApp Summarizer self-test"
_EMAIL_BODY = (
    "This is a self-test email from the WhatsApp Summarizer. "
    "If you received this, outbound email delivery is working."
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def check_gowa(config: Config, gowa) -> list[CheckResult]:
    out: list[CheckResult] = []
    for u in config.users:
        try:
            chats = gowa.list_chats(u.device)
            out.append(CheckResult(f"gowa[{u.phone}]", True, f"{len(chats)} chats reachable"))
        except Exception as e:
            out.append(CheckResult(f"gowa[{u.phone}]", False, f"{type(e).__name__}: {e}"))
    return out


def check_gemini(settings: Settings, gemini) -> list[CheckResult]:
    """Probe each configured model once (free key first, then paid). Uses the
    low-level _call to test each model directly without the long failover loop."""
    out: list[CheckResult] = []
    models = [("primary", settings.gemini_primary_model),
              ("fallback", settings.gemini_fallback_model)]
    keys = [("free", settings.gemini_key_free), ("paid", settings.gemini_key_paid)]
    for label, model in models:
        ok = False
        detail = "no Gemini API key configured"
        last_err: Exception | None = None
        for key_label, key in keys:
            if not key:
                continue
            try:
                text = gemini._call(key, model, [_GEMINI_PROBE])
                ok = True
                detail = f"{key_label} key -> {(text or '').strip()[:40]!r}"
                break
            except Exception as e:
                last_err = e
        if not ok and last_err is not None:
            detail = f"{type(last_err).__name__}: {last_err}"
        out.append(CheckResult(f"gemini[{label}:{model}]", ok, detail))
    return out


def check_email(settings: Settings, to: str, send: Callable) -> CheckResult:
    try:
        send(settings, to, _EMAIL_SUBJECT, _EMAIL_BODY)
        return CheckResult(f"email[{to}]", True, "test email sent")
    except Exception as e:
        return CheckResult(f"email[{to}]", False, f"{type(e).__name__}: {e}")


def run_selftest(config: Config, gowa, gemini, send: Callable,
                 email_to: Optional[str] = None,
                 do_gemini: bool = True, do_email: bool = True) -> list[CheckResult]:
    results: list[CheckResult] = []
    results += check_gowa(config, gowa)
    if do_gemini:
        results += check_gemini(config.settings, gemini)
    if do_email:
        recipient = email_to or (
            config.users[0].mail_to if config.users else config.settings.mail_from)
        results.append(check_email(config.settings, recipient, send))
    return results


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.selftest",
        description="Check GoWA connectivity, Gemini models, and email delivery.")
    parser.add_argument("--email-to", default=None,
                        help="Override recipient for the test email "
                             "(default: first user's mail_to).")
    parser.add_argument("--skip-email", action="store_true",
                        help="Do not send a test email.")
    parser.add_argument("--skip-gemini", action="store_true",
                        help="Do not call the Gemini models.")
    args = parser.parse_args(argv)

    configure(os.environ.get("LOG_LEVEL", "INFO"))
    try:
        config = load_config(os.environ)
    except Exception:
        log.exception("config load failed")
        sys.exit(1)

    s = config.settings
    gowa = GowaClient(s)
    gemini = GeminiClient(s)
    results = run_selftest(
        config, gowa, gemini, mailer.send,
        email_to=args.email_to,
        do_gemini=not args.skip_gemini,
        do_email=not args.skip_email,
    )

    ok_all = all(r.ok for r in results)
    for r in results:
        status = "OK  " if r.ok else "FAIL"
        print(f"[{status}] {r.name} — {r.detail}")
    print("ALL CHECKS PASSED" if ok_all else "SOME CHECKS FAILED")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
