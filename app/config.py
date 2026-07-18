from __future__ import annotations
from pathlib import Path
from typing import Mapping, Optional
import yaml
from app.models import Settings, User, Config

# Repo root (parent of the app/ package). Used to build CWD-independent
# defaults so the worker runs outside Docker without DB_PATH/USERS_FILE set.
_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB_PATH = str(_ROOT / "data" / "summarizer.db")
_DEFAULT_USERS_FILE = str(_ROOT / "config" / "users.yaml")


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
        gemini_primary_model=env.get("GEMINI_PRIMARY_MODEL", "gemini-2.5-flash"),
        gemini_fallback_model=env.get("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite"),
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
        db_path=env.get("DB_PATH") or _DEFAULT_DB_PATH,
        users_file=env.get("USERS_FILE") or _DEFAULT_USERS_FILE,
        max_email_attach_mb=int(env.get("MAX_EMAIL_ATTACH_MB", "18")),
        max_email_chunks=int(env.get("MAX_EMAIL_CHUNKS", "5")),
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
            gemini_primary_model=str(
                e.get("gemini_primary_model", settings.gemini_primary_model)),
            gemini_fallback_model=str(
                e.get("gemini_fallback_model", settings.gemini_fallback_model)),
        ))
    return users


def load_config(env: Mapping[str, str], users_path: Optional[str] = None) -> Config:
    settings = load_settings(env)
    users = load_users(users_path or settings.users_file, settings)
    return Config(settings=settings, users=users)
