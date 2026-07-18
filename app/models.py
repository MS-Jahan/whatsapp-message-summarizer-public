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
class EmailAttachment:
    filename: str
    mime_type: str
    data: bytes
    label: str


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
    gemini_primary_model: str
    gemini_fallback_model: str

    @property
    def device(self) -> str:
        return f"{self.phone}@s.whatsapp.net"


@dataclass
class Settings:
    gowa_base_url: str
    gowa_basic_auth: tuple[str, str]
    timezone: str
    scan_hour: int
    gemini_primary_model: str
    gemini_fallback_model: str
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
    max_email_attach_mb: int = 18
    max_email_chunks: int = 5


@dataclass
class Config:
    settings: Settings
    users: list[User]
