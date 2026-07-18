from __future__ import annotations
import base64
import smtplib
from email.message import EmailMessage
from typing import Optional

import markdown as _md

from app.models import EmailAttachment, Settings


class MailError(Exception):
    pass


_HTML_TEMPLATE = """\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f4f4f7;">
    <div style="max-width:640px;margin:0 auto;padding:24px 20px;
                font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
                font-size:15px;line-height:1.6;color:#1a1a1a;">
      <h2 style="margin:0 0 16px;font-size:18px;color:#075e54;">{title}</h2>
      <div style="background:#ffffff;border:1px solid #e6e6e6;border-radius:10px;
                  padding:20px 22px;">
        {body}
      </div>
    </div>
  </body>
</html>
"""


def render_html(title: str, markdown_text: str) -> str:
    """Render a Markdown summary into a self-contained HTML email body."""
    body = _md.markdown(
        markdown_text or "",
        extensions=["extra", "sane_lists", "nl2br"],
    )
    return _HTML_TEMPLATE.format(title=_escape(title), body=body)


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _send_resend(settings: Settings, to: str, subject: str, body: str,
                 html: Optional[str] = None,
                 attachments: Optional[list] = None) -> None:
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
         html: Optional[str] = None,
         attachments: Optional[list] = None) -> None:
    try:
        if settings.resend_api_key:
            _send_resend(settings, to, subject, body, html, attachments)
        else:
            _send_smtp(settings, to, subject, body, html, attachments)
    except Exception as e:
        raise MailError(str(e)) from e
