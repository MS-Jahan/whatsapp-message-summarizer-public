"""Tests for attachment batch packing and formatting."""
from app.attachments import pack_batches, format_footer
from app.models import EmailAttachment

_MB = 1024 * 1024


def _att(label, size, mime="image/jpeg"):
    """Helper to create an EmailAttachment with dummy data."""
    return EmailAttachment(
        filename=f"{label}.jpg", mime_type=mime, data=b"x" * size, label=label
    )


# pack_batches tests


def test_pack_batches_single_batch_when_under_budget():
    """When all attachments fit in one batch, return single batch."""
    atts = [_att("a", 1 * _MB), _att("b", 1 * _MB)]
    batches, oversized = pack_batches(atts, max_bytes=10 * _MB)
    assert len(batches) == 1
    assert batches[0] == atts
    assert oversized == []


def test_pack_batches_splits_when_over_budget():
    """When attachments would overflow, start new batch."""
    atts = [_att("a", 6 * _MB), _att("b", 6 * _MB), _att("c", 6 * _MB)]
    batches, oversized = pack_batches(atts, max_bytes=10 * _MB)
    # First item: 6 MB fits (6 <= 10)
    # Second item: 6+6=12 > 10, start new batch
    # Third item: 6+6=12 > 10, start new batch
    assert len(batches) == 3
    assert batches[0] == [atts[0]]
    assert batches[1] == [atts[1]]
    assert batches[2] == [atts[2]]
    assert oversized == []


def test_pack_batches_single_item_over_budget_is_oversized():
    """An item larger than max_bytes is marked oversized, not included in batches."""
    atts = [_att("huge", 20 * _MB), _att("small", 1 * _MB)]
    batches, oversized = pack_batches(atts, max_bytes=10 * _MB)
    assert oversized == [atts[0]]
    assert batches == [[atts[1]]]


def test_pack_batches_empty_input():
    """Empty input returns empty batches and oversized lists."""
    batches, oversized = pack_batches([], max_bytes=10 * _MB)
    assert batches == []
    assert oversized == []


def test_pack_batches_multiple_oversized_items():
    """Multiple oversized items are all collected in oversized list."""
    atts = [
        _att("huge1", 20 * _MB),
        _att("small", 1 * _MB),
        _att("huge2", 25 * _MB),
    ]
    batches, oversized = pack_batches(atts, max_bytes=10 * _MB)
    assert len(oversized) == 2
    assert oversized[0].label == "huge1"
    assert oversized[1].label == "huge2"
    assert batches == [[atts[1]]]


def test_pack_batches_exactly_at_limit():
    """Attachments exactly at the max_bytes limit fit in one batch."""
    atts = [_att("a", 5 * _MB), _att("b", 5 * _MB)]
    batches, oversized = pack_batches(atts, max_bytes=10 * _MB)
    assert len(batches) == 1
    assert batches[0] == atts
    assert oversized == []


def test_pack_batches_one_byte_over_limit():
    """Adding one more byte triggers a new batch."""
    atts = [_att("a", 5 * _MB), _att("b", 5 * _MB + 1)]
    batches, oversized = pack_batches(atts, max_bytes=10 * _MB)
    assert len(batches) == 2
    assert batches[0] == [atts[0]]
    assert batches[1] == [atts[1]]
    assert oversized == []


# format_footer tests


def test_format_footer_empty_when_nothing_to_report():
    """When no oversized, dropped, or extra emails, return empty string."""
    assert format_footer([], [], extra_emails=0) == ""


def test_format_footer_lists_oversized():
    """Oversized items are formatted with label, filename, and size."""
    oversized = [_att("Alice at 14:32", 20 * _MB, mime="video/mp4")]
    footer = format_footer(oversized, [], extra_emails=0)
    assert "Alice at 14:32" in footer
    assert "20.0 MB" in footer
    assert "too large" in footer.lower()
    assert "---" in footer


def test_format_footer_lists_dropped():
    """Dropped items are formatted similarly to oversized."""
    dropped = [_att("Bob at 09:00", 5 * _MB)]
    footer = format_footer([], dropped, extra_emails=0)
    assert "Bob at 09:00" in footer
    assert "5.0 MB" in footer
    assert "attachment email limit" in footer.lower()
    assert "---" in footer


def test_format_footer_lists_extra_emails_singular():
    """Single extra email uses singular form."""
    footer = format_footer([], [], extra_emails=1)
    assert "1 more attachment email follow" in footer
    assert "emails" not in footer


def test_format_footer_lists_extra_emails_plural():
    """Multiple extra emails use plural form."""
    footer = format_footer([], [], extra_emails=2)
    assert "2 more attachment emails follow" in footer


def test_format_footer_all_three_sections():
    """When all three sections present, they are formatted with separators."""
    oversized = [_att("Alice at 14:32", 20 * _MB)]
    dropped = [_att("Bob at 09:00", 5 * _MB)]
    footer = format_footer(oversized, dropped, extra_emails=3)
    assert "Alice at 14:32" in footer
    assert "Bob at 09:00" in footer
    assert "3 more attachment emails follow" in footer
    # Check sections are separated by blank lines
    lines = footer.split("\n")
    assert "---" in lines


def test_format_footer_multiple_oversized_items():
    """Multiple oversized items are all listed."""
    oversized = [
        _att("Alice at 14:32", 20 * _MB),
        _att("Charlie at 15:45", 25 * _MB),
    ]
    footer = format_footer(oversized, [], extra_emails=0)
    assert "Alice at 14:32" in footer
    assert "Charlie at 15:45" in footer
    assert "20.0 MB" in footer
    assert "25.0 MB" in footer


def test_format_footer_mb_formatting_decimal():
    """Size is formatted with 1 decimal place."""
    oversized = [_att("test", 1536 * 1024)]  # 1.5 MB
    footer = format_footer(oversized, [], extra_emails=0)
    assert "1.5 MB" in footer


def test_format_footer_mb_formatting_whole():
    """Whole MB values show .0."""
    oversized = [_att("test", 2 * _MB)]
    footer = format_footer(oversized, [], extra_emails=0)
    assert "2.0 MB" in footer
