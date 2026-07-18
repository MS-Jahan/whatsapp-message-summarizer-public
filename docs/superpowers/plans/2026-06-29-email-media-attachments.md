# Email Media Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach the images and videos already downloaded during summarization to
the daily summary email, splitting across multiple emails when the combined size
exceeds Zoho's SMTP limit, and naming any items that could not be attached.

**Architecture:** Media bytes are already downloaded once in
`app/summarizer.py::build_parts()` to feed Gemini; this plan captures the
image/video bytes (never audio) into `EmailAttachment` records as a second
return value, alongside the existing transcript-building logic. A new pure
module `app/attachments.py` greedily bin-packs those records into per-email
batches under a configurable byte budget, splitting out any single item too
large to ever fit ("oversized"). `app/mailer.py` gains attachment support for
both SMTP and Resend. `app/worker.py::process_row()` sends one email per batch
(summary + batch 1, then lightweight continuation emails for batches 2..N), with
a footer naming oversized/dropped items. Everything stays in memory — nothing is
written to local disk, so there is nothing to "clean up" on our side.

**Tech Stack:** Python 3, stdlib `email.message.EmailMessage` (SMTP attachments),
`resend` SDK (already a dependency), pytest.

**Reference spec:** `docs/superpowers/specs/2026-06-29-email-media-attachments-design.md`

## Global Constraints

- Voice notes / audio are **never** attached to email — only `image` and `video`.
- Only attach media that was already downloaded for Gemini (i.e. passed the
  existing `MAX_VIDEO_MB` / `MAX_MEDIA_ITEMS` / `MAX_TOTAL_MEDIA_MB` budgets in
  `build_parts()`). Do not add a second download path.
- Default per-email raw-attachment budget: `MAX_EMAIL_ATTACH_MB=18` (env-configurable).
- Cap continuation emails per conversation: `MAX_EMAIL_CHUNKS=5` (env-configurable);
  anything beyond the cap is named as dropped, not sent.
- Never write downloaded media to local disk — keep everything in memory end to end.
- Do not modify `app/lock.py`, `app/store.py`, `app/gowa_client.py`, `app/scanner.py`,
  `app/names.py`, `app/logging_setup.py`, `app/gemini.py`.
- Follow existing test style: hand-rolled fakes (no `unittest.mock`), `_settings(**over)`
  helper pattern, one assertion-focused test per behavior.

---

### Task 1: `EmailAttachment` model + `Settings` budget fields

**Files:**
- Modify: `app/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `EmailAttachment(filename: str, mime_type: str, data: bytes, label: str)`
  dataclass; `Settings.max_email_attach_mb: int` (default `18`);
  `Settings.max_email_chunks: int` (default `5`). Later tasks import
  `EmailAttachment` from `app.models` and read the two new `Settings` fields.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py` (check the file first for existing style/imports —
it currently tests plain dataclass construction):

```python
from app.models import EmailAttachment


def test_email_attachment_fields():
    a = EmailAttachment(filename="x.jpg", mime_type="image/jpeg",
                        data=b"BYTES", label="Alice at 14:32")
    assert a.filename == "x.jpg"
    assert a.mime_type == "image/jpeg"
    assert a.data == b"BYTES"
    assert a.label == "Alice at 14:32"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py::test_email_attachment_fields -v`
Expected: FAIL with `ImportError: cannot import name 'EmailAttachment'`

- [ ] **Step 3: Implement**

In `app/models.py`, add after the `ChatRef` dataclass (around line 24):

```python
@dataclass
class EmailAttachment:
    filename: str
    mime_type: str
    data: bytes
    label: str
```

Then in the `Settings` dataclass, add two fields at the end (after `users_file: str`,
around line 81), each with a default so every existing `Settings(...)` call site
(tests, `config.py`) keeps working unmodified:

```python
    max_email_attach_mb: int = 18
    max_email_chunks: int = 5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS (all tests, including the new one)

- [ ] **Step 5: Run full test suite to confirm no Settings-construction regressions**

Run: `python -m pytest -q`
Expected: PASS (no other test file passes `max_email_attach_mb`/`max_email_chunks`,
so the new defaulted fields must not break anything)

- [ ] **Step 6: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: add EmailAttachment model and email attachment budget settings"
```

---

### Task 2: Wire `MAX_EMAIL_ATTACH_MB` / `MAX_EMAIL_CHUNKS` env vars

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `Settings.max_email_attach_mb`, `Settings.max_email_chunks` (Task 1).
- Produces: `load_settings(env)` populates both from `MAX_EMAIL_ATTACH_MB` /
  `MAX_EMAIL_CHUNKS` env vars (defaults `18` / `5`). Later tasks (worker) read
  these off the `Settings` object returned by `load_config`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_email_attachment_defaults():
    s = load_settings(BASE_ENV)
    assert s.max_email_attach_mb == 18
    assert s.max_email_chunks == 5


def test_email_attachment_overrides():
    env = dict(BASE_ENV, MAX_EMAIL_ATTACH_MB="10", MAX_EMAIL_CHUNKS="2")
    s = load_settings(env)
    assert s.max_email_attach_mb == 10
    assert s.max_email_chunks == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_email_attachment_defaults -v`
Expected: FAIL with `AssertionError: assert 18 == 18` — no, it fails because
`Settings` already defaults to `18`/`5` from Task 1, so this part of the test
would actually pass already. The override test is the one that matters:

Run: `python -m pytest tests/test_config.py::test_email_attachment_overrides -v`
Expected: FAIL — `load_settings` ignores `MAX_EMAIL_ATTACH_MB`/`MAX_EMAIL_CHUNKS`
env vars today, so `s.max_email_attach_mb` is `18` (the dataclass default) not `10`.

- [ ] **Step 3: Implement**

In `app/config.py`, in `load_settings()`, add two lines inside the `Settings(...)`
call (after `users_file=...`, around line 60):

```python
        max_email_attach_mb=int(env.get("MAX_EMAIL_ATTACH_MB", "18")),
        max_email_chunks=int(env.get("MAX_EMAIL_CHUNKS", "5")),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat: wire MAX_EMAIL_ATTACH_MB and MAX_EMAIL_CHUNKS env vars"
```

---

### Task 3: Collect `EmailAttachment`s during summarization

**Files:**
- Modify: `app/summarizer.py`
- Test: `tests/test_summarizer.py`

**Interfaces:**
- Consumes: `EmailAttachment` (Task 1).
- Produces: `build_parts(...) -> tuple[list, list[EmailAttachment]]` (was
  `-> list`); `summarize(...) -> tuple[str, list[EmailAttachment]]` (was `-> str`).
  Task 6 (worker) consumes `summarize()`'s new 2-tuple return.

This is the highest-risk task: it changes the public return shape of two
functions. Every existing call site and test must be updated in the same task.

- [ ] **Step 1: Update existing tests for the new `build_parts` return shape**

`tests/test_summarizer.py` currently does `parts = build_parts(...)` and
indexes into `parts`. Change every call site to unpack the tuple. Apply this
exact edit to **each** of the 6 existing calls (lines 60, 74, 83, 90, 101, 111, 121
per the current file — search for `build_parts(` to find them all):

```python
# before
parts = build_parts(conv, _FakeGowa(), _USER, _settings(), resolver)
# after
parts, attachments = build_parts(conv, _FakeGowa(), _USER, _settings(), resolver)
```

For `test_text_only_transcript` (asserts `len(parts) == 1`) and
`test_image_downloaded_as_part` (asserts `len(parts) == 2`), also assert on
`attachments` explicitly:

```python
def test_text_only_transcript():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(content="hello"), _msg(content="world", is_from_me=True)])
    parts, attachments = build_parts(conv, _FakeGowa(), _USER, _settings(), _resolver())
    transcript = parts[0]
    assert "hello" in transcript and "world" in transcript
    assert len(parts) == 1  # no media
    assert attachments == []


def test_image_downloaded_as_part():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(id="img1", media_type="image", filename="p.jpg",
                              file_length=1000)])
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(), _resolver())
    assert g.downloaded == ["img1"]
    assert len(parts) == 2  # transcript + one media part
    assert len(attachments) == 1
    assert attachments[0].mime_type == "image/jpeg"
    assert attachments[0].data == b"BYTES"
```

Now add new tests for the behaviors this task introduces — audio exclusion,
video inclusion, and budget-omitted items not producing attachments:

```python
def test_audio_never_collected_as_email_attachment():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(id="a1", media_type="audio", filename="v.ogg",
                              file_length=1000)])
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(), _resolver())
    assert g.downloaded == ["a1"]       # still downloaded for Gemini
    assert attachments == []            # but never collected for email


def test_video_collected_as_email_attachment():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(id="v1", media_type="video", filename="v.mp4",
                              file_length=1000)])
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(max_video_mb=10),
                                     _resolver())
    assert len(attachments) == 1
    assert attachments[0].mime_type == "image/jpeg"  # _FakeGowa always returns this ctype


def test_video_skipped_for_gemini_produces_no_attachment():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(media_type="video", filename="v.mp4",
                              file_length=20 * 1024 * 1024)])
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(max_video_mb=10),
                                     _resolver())
    assert attachments == []


def test_budget_omitted_media_produces_no_attachment():
    msgs = [_msg(id=f"i{i}", media_type="image", filename="p.jpg", file_length=1000)
            for i in range(3)]
    conv = Conversation("a@s.whatsapp.net", "Alice", msgs)
    g = _FakeGowa()
    parts, attachments = build_parts(conv, g, _USER, _settings(max_media_items=2),
                                     _resolver())
    assert len(attachments) == 2  # only the 2 actually downloaded


def test_attachment_filename_and_label():
    conv = Conversation("a@s.whatsapp.net", "Alice",
                        [_msg(id="img1", media_type="image", filename="p.jpg",
                              sender_jid="a@s.whatsapp.net",
                              timestamp=datetime(2026, 6, 24, 14, 32, tzinfo=timezone.utc),
                              file_length=1000)])
    g = _FakeGowa()
    resolver = _resolver(contacts={"a@s.whatsapp.net": "Alice"})
    parts, attachments = build_parts(conv, g, _USER, _settings(), resolver)
    a = attachments[0]
    assert a.filename == "143200_image.jpg"
    assert a.label == "Alice at 14:32"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_summarizer.py -v`
Expected: FAIL — `build_parts` still returns a plain list, so tuple-unpacking
(`parts, attachments = ...`) raises `ValueError: too many values to unpack`
(a list of strings/Parts has more than 2 items) on every updated call site.

- [ ] **Step 3: Implement**

Replace the full contents of `app/summarizer.py` from `_mime_for` through the
end of the file with:

```python
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


def _label(msg, resolver) -> str:
    who = "Me" if msg.is_from_me else resolver.name_for_jid(msg.sender_jid)
    return f"[{msg.timestamp.isoformat()}] {who}"


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
```

Also update the import line at the top of `app/summarizer.py` (line 3) to include
`EmailAttachment`:

```python
from app.models import Conversation, EmailAttachment, User, Settings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_summarizer.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add app/summarizer.py tests/test_summarizer.py
git commit -m "feat: collect image/video EmailAttachments during summarization"
```

---

### Task 4: `app/attachments.py` — batch packing + footer text

**Files:**
- Create: `app/attachments.py`
- Test: `tests/test_attachments.py`

**Interfaces:**
- Consumes: `EmailAttachment` (Task 1).
- Produces: `pack_batches(attachments: list[EmailAttachment], max_bytes: int) ->
  tuple[list[list[EmailAttachment]], list[EmailAttachment]]` (returns
  `(batches, oversized)`); `format_footer(oversized: list[EmailAttachment],
  dropped: list[EmailAttachment], extra_emails: int) -> str` (returns markdown,
  `""` if there is nothing to report). Task 6 (worker) calls both.

- [ ] **Step 1: Write the failing test**

Create `tests/test_attachments.py`:

```python
from app.attachments import pack_batches, format_footer
from app.models import EmailAttachment

_MB = 1024 * 1024


def _att(label, size, mime="image/jpeg"):
    return EmailAttachment(filename=f"{label}.jpg", mime_type=mime,
                           data=b"x" * size, label=label)


def test_pack_batches_single_batch_when_under_budget():
    atts = [_att("a", 1 * _MB), _att("b", 1 * _MB)]
    batches, oversized = pack_batches(atts, max_bytes=10 * _MB)
    assert len(batches) == 1
    assert batches[0] == atts
    assert oversized == []


def test_pack_batches_splits_when_over_budget():
    atts = [_att("a", 6 * _MB), _att("b", 6 * _MB), _att("c", 6 * _MB)]
    batches, oversized = pack_batches(atts, max_bytes=10 * _MB)
    assert len(batches) == 2
    assert batches[0] == [atts[0]]
    assert batches[1] == [atts[1], atts[2]]
    assert oversized == []


def test_pack_batches_single_item_over_budget_is_oversized():
    atts = [_att("huge", 20 * _MB), _att("small", 1 * _MB)]
    batches, oversized = pack_batches(atts, max_bytes=10 * _MB)
    assert oversized == [atts[0]]
    assert batches == [[atts[1]]]


def test_pack_batches_empty_input():
    batches, oversized = pack_batches([], max_bytes=10 * _MB)
    assert batches == []
    assert oversized == []


def test_format_footer_empty_when_nothing_to_report():
    assert format_footer([], [], extra_emails=0) == ""


def test_format_footer_lists_oversized():
    oversized = [_att("Alice at 14:32", 20 * _MB, mime="video/mp4")]
    footer = format_footer(oversized, [], extra_emails=0)
    assert "Alice at 14:32" in footer
    assert "20.0 MB" in footer
    assert "too large" in footer.lower()


def test_format_footer_lists_dropped_and_extra_emails_note():
    dropped = [_att("Bob at 09:00", 5 * _MB)]
    footer = format_footer([], dropped, extra_emails=2)
    assert "Bob at 09:00" in footer
    assert "2" in footer
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_attachments.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.attachments'`

- [ ] **Step 3: Implement**

Create `app/attachments.py`:

```python
from __future__ import annotations
from app.models import EmailAttachment


def pack_batches(attachments: list[EmailAttachment], max_bytes: int
                 ) -> tuple[list[list[EmailAttachment]], list[EmailAttachment]]:
    """Greedily bin-pack attachments into batches no larger than max_bytes each.
    An item individually larger than max_bytes can never fit in any email; it is
    returned separately as 'oversized' rather than attached."""
    batches: list[list[EmailAttachment]] = []
    oversized: list[EmailAttachment] = []
    current: list[EmailAttachment] = []
    current_bytes = 0
    for a in attachments:
        size = len(a.data)
        if size > max_bytes:
            oversized.append(a)
            continue
        if current and current_bytes + size > max_bytes:
            batches.append(current)
            current = []
            current_bytes = 0
        current.append(a)
        current_bytes += size
    if current:
        batches.append(current)
    return batches, oversized


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def format_footer(oversized: list[EmailAttachment], dropped: list[EmailAttachment],
                  extra_emails: int) -> str:
    """Markdown footer naming any items that could not be attached, and noting
    how many follow-up emails carry the rest. Returns "" if nothing to report."""
    parts: list[str] = []
    if oversized:
        parts.append("**Not attached (too large for email):**")
        for a in oversized:
            parts.append(f"- {a.label}: {a.filename} ({_mb(len(a.data))})")
    if dropped:
        parts.append("**Not attached (attachment email limit reached):**")
        for a in dropped:
            parts.append(f"- {a.label}: {a.filename} ({_mb(len(a.data))})")
    if extra_emails:
        plural = "s" if extra_emails != 1 else ""
        parts.append(f"_{extra_emails} more attachment email{plural} follow._")
    if not parts:
        return ""
    return "\n\n---\n" + "\n".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_attachments.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/attachments.py tests/test_attachments.py
git commit -m "feat: add attachment batch packing and omitted-items footer"
```

---

### Task 5: Mailer attachment support (SMTP + Resend)

**Files:**
- Modify: `app/mailer.py`
- Test: `tests/test_mailer.py`

**Interfaces:**
- Consumes: `EmailAttachment` (Task 1).
- Produces: `send(settings, to, subject, body, html=None, attachments=None) ->
  None` (new optional 6th param); `_send_smtp(...)` / `_send_resend(...)` same
  new param. Task 6 (worker) passes `attachments=batch` through `Deps.mailer_send`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mailer.py` (add `from app.models import EmailAttachment` to
the imports at the top):

```python
def test_smtp_adds_attachments(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    atts = [EmailAttachment(filename="p.jpg", mime_type="image/jpeg",
                            data=b"BYTES", label="Alice at 14:32")]
    mailer._send_smtp(_settings(smtp_port=587), "to@x.com", "Subj", "Body",
                      attachments=atts)
    srv = _FakeSMTP.instances[-1]
    found = [p for p in srv.sent_msg.walk() if p.get_content_type() == "image/jpeg"]
    assert len(found) == 1
    assert found[0].get_filename() == "p.jpg"
    assert found[0].get_payload(decode=True) == b"BYTES"


def test_smtp_no_attachments_param_is_backward_compatible(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    mailer._send_smtp(_settings(smtp_port=587), "to@x.com", "Subj", "Body")
    srv = _FakeSMTP.instances[-1]
    assert srv.sent is True


def test_resend_includes_base64_attachments(monkeypatch):
    import sys, types as _types
    fake_resend = _types.ModuleType("resend")
    fake_resend.api_key = None
    sent_payload = {}

    class _Emails:
        @staticmethod
        def send(payload):
            sent_payload.update(payload)
    fake_resend.Emails = _Emails
    monkeypatch.setitem(sys.modules, "resend", fake_resend)

    atts = [EmailAttachment(filename="p.jpg", mime_type="image/jpeg",
                            data=b"BYTES", label="Alice at 14:32")]
    mailer._send_resend(_settings(resend_api_key="re_x"), "to@x.com", "Subj",
                        "Body", attachments=atts)
    assert "attachments" in sent_payload
    assert sent_payload["attachments"][0]["filename"] == "p.jpg"
    import base64
    assert base64.b64decode(sent_payload["attachments"][0]["content"]) == b"BYTES"


def test_send_passes_attachments_through_to_smtp(monkeypatch):
    calls = {}
    monkeypatch.setattr(mailer, "_send_resend",
                        lambda s, to, subj, body, html=None, attachments=None:
                            calls.setdefault("resend", True))
    monkeypatch.setattr(mailer, "_send_smtp",
                        lambda s, to, subj, body, html=None, attachments=None:
                            calls.setdefault("smtp_atts", attachments))
    atts = [EmailAttachment(filename="p.jpg", mime_type="image/jpeg",
                            data=b"X", label="L")]
    mailer.send(_settings(resend_api_key=""), "u@x.com", "Subj", "Body",
               attachments=atts)
    assert calls["smtp_atts"] == atts
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mailer.py -v`
Expected: FAIL — `_send_smtp()` / `_send_resend()` / `send()` raise
`TypeError: ... got an unexpected keyword argument 'attachments'`

- [ ] **Step 3: Implement**

In `app/mailer.py`, add the import at the top (after `from app.models import
Settings`):

```python
from app.models import EmailAttachment
```

Replace `_send_resend`, `_send_smtp`, and `send` with:

```python
def _send_resend(settings: Settings, to: str, subject: str, body: str,
                 html: Optional[str] = None,
                 attachments: Optional[list] = None) -> None:
    import base64
    import resend
    resend.api_key = settings.resend_api_key
    payload = {"from": settings.mail_from, "to": [to],
               "subject": subject, "text": body}
    if html:
        payload["html"] = html
    if attachments:
        payload["attachments"] = [
            {"filename": a.filename, "content": base64.b64encode(a.data).decode()}
            for a in attachments]
    resend.Emails.send(payload)


def _send_smtp(settings: Settings, to: str, subject: str, body: str,
               html: Optional[str] = None,
               attachments: Optional[list] = None) -> None:
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    for a in attachments or []:
        maintype, _, subtype = a.mime_type.partition("/")
        msg.add_attachment(a.data, maintype=maintype or "application",
                           subtype=subtype or "octet-stream", filename=a.filename)
    # Port 465 is implicit SSL (SMTPS) → connect with SMTP_SSL. Other ports
    # (e.g. 587) use plaintext + optional STARTTLS upgrade.
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30) as srv:
            if settings.smtp_user:
                srv.login(settings.smtp_user, settings.smtp_pass)
            srv.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as srv:
            if settings.smtp_tls:
                srv.starttls()
            if settings.smtp_user:
                srv.login(settings.smtp_user, settings.smtp_pass)
            srv.send_message(msg)


def send(settings: Settings, to: str, subject: str, body: str,
         html: Optional[str] = None, attachments: Optional[list] = None) -> None:
    try:
        if settings.resend_api_key:
            _send_resend(settings, to, subject, body, html, attachments)
        else:
            _send_smtp(settings, to, subject, body, html, attachments)
    except Exception as e:
        raise MailError(str(e)) from e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mailer.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add app/mailer.py tests/test_mailer.py
git commit -m "feat: support email attachments in SMTP and Resend senders"
```

---

### Task 6: Wire batching + sending into `process_row`

**Files:**
- Modify: `app/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `summarize() -> tuple[str, list[EmailAttachment]]` (Task 3);
  `pack_batches()` / `format_footer()` (Task 4); `mailer.send(..., attachments=)`
  (Task 5); `Settings.max_email_attach_mb` / `Settings.max_email_chunks` (Tasks 1-2).
- Produces: `Deps.mailer_send: Callable[[str, str, str, Optional[str],
  Optional[list]], None]` (was 4-arg, now 5-arg with `attachments` defaulting to
  `None`); `process_row()` sends 1 email if there are no/small attachments, or
  N emails (1 summary + N-1 continuations) when attachments need splitting.

- [ ] **Step 1: Update existing test fakes for the new `mailer_send` signature**

In `tests/test_worker.py`, update both `_deps()` and the inline `Deps(...)` in
`test_failed_summary_marks_failed_and_alerts` to accept the new `attachments`
parameter (default `None`), and update `_FakeGemini.generate` is unaffected, but
`_FakeGowa.download_media` already returns `(b"", "image/jpeg")` (line 45) which
is fine. The `_FakeGemini` class itself doesn't need to change — the new
`(text, attachments)` tuple is produced by `summarize()`, not by `_FakeGemini`.

```python
# tests/test_worker.py — replace _deps()
def _deps(gowa, store, sent, alerts):
    return Deps(gowa=gowa, gemini=_FakeGemini(), store=store,
                mailer_send=lambda to, subj, body, html=None, attachments=None:
                    sent.append((to, subj, body, html, attachments)),
                notify=lambda text: alerts.append(text))
```

```python
# tests/test_worker.py — replace the Deps(...) call inside
# test_failed_summary_marks_failed_and_alerts
    sent, alerts = [], []
    deps = Deps(gowa=_FakeGowa(chats, msgs), gemini=_BoomGemini(), store=store,
                mailer_send=lambda *a, **k: sent.append(a), notify=lambda t: alerts.append(t))
```

Update the 5-tuple assertions in `test_run_once_enqueues_and_emails` (indices
shift because of the appended `attachments` element — the existing 4 assertions
on `sent[0][0..3]` stay valid since `attachments` is appended at index 4, not
inserted earlier):

```python
    assert sent[0][0] == "x@y.com"
    assert "Alice" in sent[0][1] and "2026-06-24" in sent[0][1]
    assert sent[0][2] == "SUMMARY"            # plain-text body
    assert sent[0][3] and "SUMMARY" in sent[0][3]  # html body present
```

No changes needed to those four lines — they already index `0..3`, leaving
the new `attachments` at index `4` unchecked by old tests (fine).

- [ ] **Step 2: Write new failing tests for attachment batching/sending**

Add to `tests/test_worker.py`:

```python
from app.models import EmailAttachment


class _MediaGowa(_FakeGowa):
    """Like _FakeGowa but download_media returns distinct, sized payloads
    keyed by msg_id so tests can control attachment sizes."""
    def __init__(self, chats, msgs, payloads):
        super().__init__(chats, msgs)
        self._payloads = payloads  # msg_id -> (bytes, content_type)
    def download_media(self, device, msg_id, chat_jid):
        return self._payloads[msg_id]


def _img_msg(msg_id, size, ts_hour=10):
    return Message(msg_id, "a@s.whatsapp.net", "a@s.whatsapp.net", False,
                   datetime(2026, 6, 24, ts_hour, tzinfo=ZoneInfo("Asia/Dhaka")),
                   "", "image", "p.jpg", size)


def test_process_row_attaches_small_media_in_one_email(tmp_path):
    tz = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 6, 24, 22, 30, tzinfo=tz)
    chats = [ChatRef("a@s.whatsapp.net", "Alice", datetime(2026, 6, 24, 10, tzinfo=tz))]
    msgs = {"a@s.whatsapp.net": [_img_msg("img1", 1024)]}
    payloads = {"img1": (b"X" * 1024, "image/jpeg")}
    store = Store(str(tmp_path / "t.db"))
    user = User("8801", "x@y.com", 22, "m", "m2")
    cfg = Config(settings=_settings(), users=[user])
    sent, alerts = [], []
    deps = _deps(_MediaGowa(chats, msgs, payloads), store, sent, alerts)
    stats = run_once(cfg, deps, now)
    assert stats["processed"] == 1
    assert len(sent) == 1
    to, subj, body, html, attachments = sent[0]
    assert len(attachments) == 1
    assert attachments[0].filename.endswith(".jpg")


def test_process_row_splits_across_multiple_emails_over_budget(tmp_path):
    tz = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 6, 24, 22, 30, tzinfo=tz)
    chats = [ChatRef("a@s.whatsapp.net", "Alice", datetime(2026, 6, 24, 10, tzinfo=tz))]
    big = 4 * 1024 * 1024  # 4 MB each; budget below is 10 MB
    msgs = {"a@s.whatsapp.net": [_img_msg("i1", big), _img_msg("i2", big),
                                  _img_msg("i3", big)]}
    payloads = {f"i{i}": (b"X" * big, "image/jpeg") for i in (1, 2, 3)}
    store = Store(str(tmp_path / "t.db"))
    user = User("8801", "x@y.com", 22, "m", "m2")
    settings = _settings(max_email_attach_mb=10)
    cfg = Config(settings=settings, users=[user])
    sent, alerts = [], []
    deps = _deps(_MediaGowa(chats, msgs, payloads), store, sent, alerts)
    stats = run_once(cfg, deps, now)
    assert stats["processed"] == 1
    # pack_batches greedily fills: i1(4MB)+i2(4MB)=8MB fits under 10MB,
    # +i3 would be 12MB so i3 starts a new batch -> batch1=[i1,i2], batch2=[i3]
    assert len(sent) == 2
    assert len(sent[0][4]) == 2   # first email carries batch1
    assert len(sent[1][4]) == 1   # continuation email carries batch2
    assert "attachments 2/2" in sent[1][1]  # subject marks continuation


def test_process_row_names_oversized_item_in_footer(tmp_path):
    tz = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 6, 24, 22, 30, tzinfo=tz)
    chats = [ChatRef("a@s.whatsapp.net", "Alice", datetime(2026, 6, 24, 10, tzinfo=tz))]
    huge = 50 * 1024 * 1024
    msgs = {"a@s.whatsapp.net": [_img_msg("i1", huge)]}
    payloads = {"i1": (b"X" * huge, "image/jpeg")}
    store = Store(str(tmp_path / "t.db"))
    user = User("8801", "x@y.com", 22, "m", "m2")
    settings = _settings(max_email_attach_mb=18)
    cfg = Config(settings=settings, users=[user])
    sent, alerts = [], []
    deps = _deps(_MediaGowa(chats, msgs, payloads), store, sent, alerts)
    stats = run_once(cfg, deps, now)
    assert stats["processed"] == 1
    assert len(sent) == 1
    assert sent[0][4] is None or sent[0][4] == []  # nothing attachable
    assert "too large" in sent[0][2].lower()  # named in plain-text body
```

Note: `_settings()` in `tests/test_worker.py` must accept `**over` like the
other test files' helpers do today it does not — check the current signature
(`def _settings():` with no params, around line 28). Update it to accept
overrides, matching the pattern already used in `test_mailer.py` /
`test_summarizer.py`:

```python
def _settings(**over):
    base = dict(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_primary_model="m", gemini_fallback_model="m2",
        gemini_key_free="f", gemini_key_paid="p",
        max_chat_attempts=2, max_video_mb=10, max_media_items=30, max_total_media_mb=40,
        resend_api_key="", smtp_host="", smtp_port=587, smtp_user="", smtp_pass="",
        smtp_tls=True, mail_from="b@x.com", telegram_bot_token="t", telegram_chat_id="1",
        log_level="INFO", db_path=":x", users_file="u")
    base.update(over)
    return Settings(**base)
```

(All existing call sites `_settings()` with no args keep working unchanged.)

Also update `_FakeGemini.generate` is unchanged, but note `summarize()` now
needs `_FakeGemini` to keep returning a plain string — that's still correct
since `summarize()` itself wraps Gemini's string return into the
`(text, attachments)` tuple; no fake changes needed there.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_worker.py -v`
Expected: FAIL — `process_row` still calls the old 4-arg `mailer_send` and the
old `summarize()` 1-value return, so unpacking `to, subj, body, html, attachments
= sent[0]` raises `ValueError: not enough values to unpack`.

- [ ] **Step 4: Implement**

In `app/worker.py`, update imports (add `attachments` module and `EmailAttachment`
is not needed directly here):

```python
from app import lock, mailer, notifier
from app.attachments import format_footer, pack_batches
from app.mailer import render_html
```

Update the `Deps` dataclass field (around line 31):

```python
    mailer_send: Callable[[str, str, str, Optional[str], Optional[list]], None]
```

Replace `process_row()` with:

```python
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
```

Update `main()`'s `Deps(...)` construction (around line 137):

```python
    deps = Deps(
        gowa=gowa, gemini=gemini, store=store,
        mailer_send=lambda to, subj, body, html=None, attachments=None:
            mailer.send(s, to, subj, body, html, attachments),
        notify=lambda text: notifier.notify(s, text),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_worker.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS, all tests in the repo

- [ ] **Step 7: Commit**

```bash
git add app/worker.py tests/test_worker.py
git commit -m "feat: split summary emails across attachment batches with omitted-item footer"
```

---

### Task 7: Document new env vars

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing (docs only).
- Produces: nothing consumed by other tasks; this is the last task.

- [ ] **Step 1: Add to `.env.example`**

In `.env.example`, after the `MAX_TOTAL_MEDIA_MB=40` line (line 18), add:

```
MAX_EMAIL_ATTACH_MB=18
MAX_EMAIL_CHUNKS=5
```

- [ ] **Step 2: Add to `README.md`**

In the env var table in `README.md`, after the `MAX_TOTAL_MEDIA_MB` row (line 66),
add:

```
| `MAX_EMAIL_ATTACH_MB` | no | `18` | Max raw attachment bytes per email (Zoho SMTP-safe budget) |
| `MAX_EMAIL_CHUNKS` | no | `5` | Max number of attachment emails per conversation/day; excess items are named, not sent |
```

- [ ] **Step 3: Commit**

```bash
git add .env.example README.md
git commit -m "docs: document MAX_EMAIL_ATTACH_MB and MAX_EMAIL_CHUNKS"
```

---

## Final verification

- [ ] Run: `python -m pytest -q`
  Expected: all tests pass, no warnings about unused fakes.
- [ ] Run: `grep -rn "mailer_send=lambda to, subj, body, html=None)" app/` (old 4-arg
  signature) — expect **no matches**, confirming every call site was updated.
