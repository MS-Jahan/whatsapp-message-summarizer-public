from __future__ import annotations
import logging
from app.models import Conversation, EmailAttachment, User, Settings

log = logging.getLogger(__name__)

_PROMPT = (
    "You are summarizing one WhatsApp conversation from a single day. "
    "Write a concise summary in clear English covering the key points, "
    "decisions, questions, and any action items. Include what is said in any "
    "attached audio and images. Do not invent details. If media was skipped, "
    "note that briefly.\n"
    "Each transcript line is prefixed with '[time] Name:' — refer to people by "
    "that name (the account owner is 'Me'). Never write 'sender' or 'receiver'.\n"
    "Output ONLY the summary content itself. Do NOT add any preamble, greeting, "
    "sign-off, or meta commentary such as 'Here is a summary', 'Sure', "
    "'Here's what was discussed', or 'Let me know if you need more'. Start "
    "directly with the substance.\n\n"
    "Conversation '{name}':\n{transcript}\n"
)

_MB = 1024 * 1024


def _is_text_part(part) -> bool:
    return isinstance(part, str)


def _mime_for(media_type: str, ctype: str) -> str:
    """Normalize the download Content-Type to a Gemini-accepted MIME type.
    WhatsApp voice notes come back as 'application/ogg', which Gemini rejects;
    they are Ogg/Opus audio, so map to 'audio/ogg'. Likewise coerce image/video
    when the server returns a generic type."""
    ctype = (ctype or "").split(";")[0].strip().lower()
    if media_type == "audio" and not ctype.startswith("audio/"):
        return "audio/ogg"
    if media_type == "image" and not ctype.startswith("image/"):
        return "image/jpeg"
    if media_type == "video" and not ctype.startswith("video/"):
        return "video/mp4"
    return ctype or "application/octet-stream"


def _label(msg, resolver) -> str:
    who = "Me" if msg.is_from_me else resolver.name_for_jid(msg.sender_jid)
    return f"[{msg.timestamp.isoformat()}] {who}"


_EXT_BY_MIME = {
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
    "video/mp4": "mp4", "video/3gpp": "3gp",
}


def _attachment_filename(msg, mime: str) -> str:
    ext = _EXT_BY_MIME.get(mime, mime.split("/")[-1] or "bin")
    ts = msg.timestamp.strftime("%H%M%S")
    return f"{ts}_{msg.media_type}.{ext}"


def _attachment_label(msg, resolver) -> str:
    who = "Me" if msg.is_from_me else resolver.name_for_jid(msg.sender_jid)
    return f"{who} at {msg.timestamp.strftime('%H:%M')}"


def build_parts(conversation: Conversation, gowa, user: User, settings: Settings,
                resolver) -> tuple[list, list]:
    lines: list[str] = []
    media_parts: list = []
    email_attachments: list = []
    item_count = 0
    total_bytes = 0
    max_items = settings.max_media_items
    max_total = settings.max_total_media_mb * _MB

    # For 1:1 chats the partner's resolved name is the conversation name; seed it
    # so per-message labels read as the contact rather than a bare number.
    if conversation.chat_jid.endswith("@s.whatsapp.net"):
        resolver.contacts.setdefault(conversation.chat_jid, conversation.name)

    for msg in conversation.messages:
        label = _label(msg, resolver)
        mt = msg.media_type
        if mt in ("", "text"):
            content = resolver.rewrite_mentions(msg.content)
            if content:
                lines.append(f"{label}: {content}")
            continue
        if mt == "video":
            if msg.file_length > settings.max_video_mb * _MB:
                mb = msg.file_length // _MB
                lines.append(f"{label}: [video skipped: {mb} MB > limit]")
                continue
            item_count, total_bytes = _try_add(
                gowa, user, msg, media_parts, email_attachments, label, lines,
                resolver, item_count, total_bytes, max_items, max_total, is_video=True)
            continue
        if mt in ("audio", "image"):
            item_count, total_bytes = _try_add(
                gowa, user, msg, media_parts, email_attachments, label, lines,
                resolver, item_count, total_bytes, max_items, max_total)
            continue
        if mt == "call":
            lines.append(f"{label}: [call]")
        else:
            lines.append(f"{label}: [{mt}: {msg.filename or ''}]")

    transcript = "\n".join(lines) if lines else "(no text messages)"
    prompt = _PROMPT.format(name=conversation.name, transcript=transcript)
    return [prompt, *media_parts], email_attachments


def _try_add(gowa, user, msg, media_parts, email_attachments, label, lines,
             resolver, item_count, total_bytes, max_items, max_total, is_video=False):
    """Download a media item if within budget; else add a text note. Never raises.
    Image/video downloads are also captured as EmailAttachment records for the
    summary email; audio is fed to Gemini only, never attached to email."""
    if item_count >= max_items or total_bytes + msg.file_length > max_total:
        lines.append(f"{label}: [{msg.media_type} omitted — more media omitted to stay within budget]")
        return item_count, total_bytes
    try:
        data, ctype = gowa.download_media(user.device, msg.id, msg.chat_jid)
    except Exception as e:
        log.warning("media download failed for %s: %s", msg.id, e)
        if is_video:
            lines.append(f"{label}: [video could not be processed]")
        else:
            lines.append(f"{label}: [{msg.media_type} could not be downloaded]")
        return item_count, total_bytes
    from google.genai import types
    mime = _mime_for(msg.media_type, ctype)
    media_parts.append(types.Part.from_bytes(data=data, mime_type=mime))
    lines.append(f"{label}: [{msg.media_type} attached]")
    if msg.media_type in ("image", "video"):
        email_attachments.append(EmailAttachment(
            filename=_attachment_filename(msg, mime), mime_type=mime,
            data=data, label=_attachment_label(msg, resolver)))
    return item_count + 1, total_bytes + len(data)


def summarize(conversation: Conversation, gowa, gemini, user: User, settings: Settings,
              resolver) -> tuple[str, list]:
    parts, attachments = build_parts(conversation, gowa, user, settings, resolver)
    text = gemini.generate(parts, user.gemini_primary_model, user.gemini_fallback_model)
    return text, attachments
