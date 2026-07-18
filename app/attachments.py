"""Attachment batch packing and formatting utilities."""
from __future__ import annotations

from app.models import EmailAttachment


def pack_batches(
    attachments: list[EmailAttachment], max_bytes: int
) -> tuple[list[list[EmailAttachment]], list[EmailAttachment]]:
    """Greedily bin-pack attachments into batches no larger than max_bytes each.

    An item individually larger than max_bytes can never fit in any email; it is
    returned separately as 'oversized' rather than attached.

    Args:
        attachments: List of attachments to pack
        max_bytes: Maximum size in bytes for each batch

    Returns:
        (batches, oversized) where:
        - batches: list of lists, each inner list is one batch
        - oversized: list of items too large to ever fit in any batch
    """
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
    """Format bytes as MB with 1 decimal place."""
    return f"{n / (1024 * 1024):.1f} MB"


def format_footer(
    oversized: list[EmailAttachment],
    dropped: list[EmailAttachment],
    extra_emails: int,
) -> str:
    """Markdown footer naming any items that could not be attached.

    Formats sections for oversized items, dropped items, and extra emails,
    separated by blank lines with a separator line before them.

    Args:
        oversized: Items too large for any email
        dropped: Items dropped due to attachment email limit
        extra_emails: Number of follow-up emails carrying the rest

    Returns:
        Markdown-formatted footer string, or "" if nothing to report
    """
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
