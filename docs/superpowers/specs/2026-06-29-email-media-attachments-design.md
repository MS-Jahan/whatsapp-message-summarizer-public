# Email Media Attachments — Design Doc

**Date:** 2026-06-29
**Status:** Draft / awaiting decisions
**Goal:** Attach the images and videos from each daily conversation summary to the
summary email. Voice notes / audio are **never** attached (transcribed by Gemini
already). Handle Zoho Mail size limits by chunking across multiple emails and
naming any items that had to be omitted.

---

## 1. Requirements (from product owner)

- One email per conversation per day, as today.
- Attach **only `image` and `video`** media. **Never** `audio` / voice notes.
- Pipeline per item: **download → attach → send → remove the downloaded copy**.
- Respect Zoho Mail attachment size limits.
- If combined attachments exceed the per-email limit → **chunk** into multiple
  emails (summary + first batch, then continuation emails for the rest).
- If a single item (e.g. a large video) is too big to ever fit → **do not attach
  it**, but **name it in the email** so the recipient knows it was left out.

---

## 2. Zoho Mail size limits (researched 2026-06-29)

The product owner uses **Zoho Mail Plus ($1/mo)**, sending via **SMTP** (see
`app/mailer.py` `_send_smtp`).

Findings:

- **SMTP outgoing default is 20 MB per mail.** This is the binding constraint for
  this app, since we send via SMTP, not the web composer.
- The absolute message ceiling is **25 MB including the "MIME quotient"**, where
  the MIME quotient is ~30% overhead on the raw attachment bytes (base64 +
  headers). So 25 MB message ≈ **~19 MB of raw attachment bytes**.
- Web UI / "Huge Attachments" feature allows up to 250 MB–1 GB depending on plan,
  but that is a **web composer link-attachment** feature, **not available over
  SMTP** — irrelevant to this app.
- The product owner's "more than 25 MB cannot be sent" recollection is roughly
  right but optimistic for SMTP; **20 MB total message size is the safe ceiling.**

**Design decision:** budget on the **raw bytes**, not the message size, and stay
well under. Default **per-email raw attachment budget = 18 MB**
(18 MB raw × 1.30 MIME ≈ 23.4 MB message — under the 25 MB hard ceiling, with the
20 MB SMTP soft limit handled by making the budget configurable; the operator can
lower it to e.g. 14 MB if Zoho rejects). Make it an env var:
`MAX_EMAIL_ATTACH_MB` (default `18`).

Sources:
- https://www.zoho.com/mail/help/attachments.html
- https://help.zoho.com/portal/en/community/topic/what-are-the-limits-of-sending-mails-via-smtp
- https://help.zoho.com/portal/en/community/topic/email-restrictions-for-outgoing-mail-and-maximum-attachment-size
- https://www.zoho.com/mail/help/adminconsole/rates-and-limits.html

---

## 3. Current state (what exists today)

- `app/summarizer.py` `build_parts()` already **downloads** image/video/audio
  bytes via `gowa.download_media()` to feed Gemini, then **discards** them. It
  enforces `MAX_VIDEO_MB`, `MAX_MEDIA_ITEMS`, `MAX_TOTAL_MEDIA_MB` for the *Gemini*
  budget (separate concern from the *email* budget).
- `app/gowa_client.py` `download_media()` returns `(bytes, content_type)`
  **in memory**. It first calls GoWA's `/message/{id}/download` (which writes a
  file on the **GoWA host**, returning `file_path`), then GETs that file.
- `app/mailer.py` `send()` / `_send_smtp()` / `_send_resend()` send **text + HTML
  only** — no attachment support yet.
- `app/worker.py` `process_row()` builds the summary, renders HTML, sends one
  email, marks the row done.

**Key reuse opportunity:** media is already downloaded once during
summarization. Capturing image/video bytes there avoids a second download.

---

## 4. Decisions

1. **"Remove them from the server" = our server, not GoWA's.** Confirmed
   2026-06-29. Our worker holds media bytes **in memory only** and never writes
   them to local disk — so "remove from server" is satisfied by construction
   (nothing to delete). GoWA-side temp-file cleanup is out of scope; not our
   concern.

2. **Chunk email subject.** Continuation emails use a suffixed subject, e.g.
   `"{name} — {date} (attachments 2/3)"`. No threading (simpler, SMTP-portable).

3. **Resend path — confirmed in scope.** Add attachment support to both
   `_send_smtp` and `_send_resend` in `app/mailer.py`.

4. **Per-email attachment budget — confirmed `18 MB`.**
   `MAX_EMAIL_ATTACH_MB` default `18`.

5. **Item identification in "omitted" notes.** Use sender name + timestamp +
   filename, e.g. `"video from Alice at 14:32 (movie.mp4, 47 MB) — too large to
   attach"`.

---

## 5. Proposed design

### 5.1 Collect attachments during summarization

Extend `build_parts()` (or a sibling pass) to also return the **image/video**
items it downloaded, as a list of attachment records — reusing the already-fetched
bytes. Audio is fed to Gemini but **not** collected for email.

```
@dataclass
class EmailAttachment:
    filename: str        # safe filename, e.g. "1432-alice.jpg"
    mime_type: str       # image/jpeg, video/mp4, ...
    data: bytes
    label: str           # human label for "omitted" notes: "Alice, 14:32"
```

`summarize()` returns `(summary_text, list[EmailAttachment])`. (Or a small result
dataclass to avoid a breaking tuple.)

### 5.2 Pack attachments into email batches

New helper `pack_batches(attachments, max_bytes) -> list[list[EmailAttachment]]`:

- Greedy bin-packing by raw byte size, `max_bytes = MAX_EMAIL_ATTACH_MB * MB`.
- Any single item **larger than `max_bytes`** → cannot fit → goes to an
  **`oversized` list** (never attached, only named in the email body).
- Returns ordered batches + the oversized list.

### 5.3 Send: summary email + continuation emails

In `process_row()`:

- **Email 1:** summary HTML + batch 1 attachments. Append a footer listing:
  - oversized items that were omitted, and
  - "N more attachments sent in follow-up emails" if `len(batches) > 1`.
- **Emails 2..N:** minimal body ("Attachments k/N for {name} — {date}") + batch k.
- If there are zero attachments, behave exactly as today (one email, no footer).

### 5.4 Mailer attachment support

Extend `mailer.send()` signature with `attachments: list[EmailAttachment] | None`:

- `_send_smtp`: `msg.add_attachment(data, maintype, subtype, filename=...)` per item.
- `_send_resend`: add `payload["attachments"] = [{"filename", "content"(base64)}]`.

### 5.5 Config

- New env `MAX_EMAIL_ATTACH_MB` (default `18`) on `Settings` + `load_settings()`.
- Independent of the existing Gemini-side `MAX_*` budgets.

---

## 6. Edge cases

- **Single oversized video** → named in footer, not attached. (Already partly
  handled for Gemini by `MAX_VIDEO_MB`; email budget is separate and may differ.)
- **Many small images** exceeding one email → split across batches.
- **Download failure for an item** → skip it, note "could not be attached" in
  footer (mirror existing Gemini-side handling).
- **Total media is huge** (e.g. 200 MB) → could mean many continuation emails.
  Consider a hard cap `MAX_EMAIL_CHUNKS` (default e.g. `5`); beyond it, name the
  rest as omitted rather than sending 20 emails. **Recommendation: add this cap.**
- **MIME inflation** pushing a "within budget" batch over Zoho's message ceiling →
  budget is on raw bytes with headroom (18 MB raw ≈ 23 MB message); keep the
  default conservative and configurable.

---

## 7. Implementation phases (for the eventual plan doc)

1. `EmailAttachment` model + collect image/video bytes in summarizer (reuse
   existing download); return `(summary, attachments)`. Unit-tested.
2. `pack_batches()` + oversized split. Pure function, unit-tested.
3. Mailer attachment support (SMTP + Resend). Unit-tested with a fake SMTP.
4. Wire into `process_row()`: footer text, continuation emails, chunk cap.
5. Config: `MAX_EMAIL_ATTACH_MB`, `MAX_EMAIL_CHUNKS`; docs in README + `.env.example`.
6. (If confirmed) GoWA temp-file cleanup investigation.

Each phase is independently testable; no schema/DB changes required.

---

## 8. Status

All blocking decisions resolved 2026-06-29 (see §4). Ready to move to an
implementation plan (`docs/superpowers/plans/`).
