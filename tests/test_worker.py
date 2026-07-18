from datetime import datetime
from zoneinfo import ZoneInfo
from app.worker import run_once, Deps, _display_name
from app.models import Config, Settings, User, Conversation, Message, ChatRef, QueueRow
from app.store import Store


def _row(chat_jid, name):
    return QueueRow(date="2026-06-24", device="d", chat_jid=chat_jid, name=name,
                    status="pending", attempts=0)


def test_display_name_prefers_real_list_name():
    row = _row("8801700000005@s.whatsapp.net", "Md. Sarwar Jahan Sabit")
    assert _display_name(row, "Contact") == "Md. Sarwar Jahan Sabit"


def test_display_name_uses_resolved_for_group_placeholder():
    row = _row("120363420800380236@g.us", "Group 120363420800380236")
    assert _display_name(row, "team@vendy.Ltd") == "team@vendy.Ltd"


def test_display_name_falls_back_to_local_when_only_phone():
    row = _row("8801700000004@s.whatsapp.net", "8801700000004")
    assert _display_name(row, "8801700000004") == "8801700000004"


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


class _FakeGowa:
    def __init__(self, chats, msgs):
        self._chats = chats; self._msgs = msgs
    def list_chats(self, device): return self._chats
    def get_messages(self, device, jid, since, until): return self._msgs.get(jid, [])
    def resolve_name(self, device, jid): return "Alice"
    def download_media(self, *a): return b"", "image/jpeg"
    def contacts_map(self, device): return {}
    def group_participants(self, device, jid): return []


class _MediaGowa(_FakeGowa):
    """Like _FakeGowa but download_media returns distinct, sized payloads
    keyed by msg_id so tests can control attachment sizes."""
    def __init__(self, chats, msgs, payloads):
        super().__init__(chats, msgs)
        self._payloads = payloads  # msg_id -> (bytes, content_type)
    def download_media(self, device, msg_id, chat_jid):
        return self._payloads[msg_id]


class _FakeGemini:
    def generate(self, parts, primary, fallback): return "SUMMARY"


def _deps(gowa, store, sent, alerts):
    return Deps(gowa=gowa, gemini=_FakeGemini(), store=store,
                mailer_send=lambda to, subj, body, html=None, attachments=None:
                    sent.append((to, subj, body, html, attachments)),
                notify=lambda text: alerts.append(text))


def _img_msg(msg_id, size, ts_hour=10):
    return Message(msg_id, "a@s.whatsapp.net", "a@s.whatsapp.net", False,
                   datetime(2026, 6, 24, ts_hour, tzinfo=ZoneInfo("Asia/Dhaka")),
                   "", "image", "p.jpg", size)


def test_run_once_enqueues_and_emails(tmp_path):
    tz = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 6, 24, 22, 30, tzinfo=tz)
    chats = [ChatRef("a@s.whatsapp.net", "Alice", datetime(2026, 6, 24, 10, tzinfo=tz))]
    msgs = {"a@s.whatsapp.net": [Message("m", "a@s.whatsapp.net", "a@s.whatsapp.net",
            False, datetime(2026, 6, 24, 10, tzinfo=tz), "hi", "", "", 0)]}
    store = Store(str(tmp_path / "t.db"))
    user = User("8801", "x@y.com", 22, "m", "m2")
    cfg = Config(settings=_settings(), users=[user])
    sent, alerts = [], []
    deps = _deps(_FakeGowa(chats, msgs), store, sent, alerts)
    stats = run_once(cfg, deps, now)
    assert stats["enqueued"] == 1 and stats["processed"] == 1
    assert sent[0][0] == "x@y.com"
    assert "Alice" in sent[0][1] and "2026-06-24" in sent[0][1]
    assert sent[0][2] == "SUMMARY"            # plain-text body
    assert sent[0][3] and "SUMMARY" in sent[0][3]  # html body present
    assert store.next_batch("2026-06-24", max_attempts=2) == []


def test_run_once_before_scan_hour_does_nothing(tmp_path):
    tz = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 6, 24, 9, 0, tzinfo=tz)
    store = Store(str(tmp_path / "t.db"))
    cfg = Config(settings=_settings(), users=[User("8801", "x@y.com", 22, "m", "m2")])
    sent, alerts = [], []
    deps = _deps(_FakeGowa([], {}), store, sent, alerts)
    stats = run_once(cfg, deps, now)
    assert stats == {"enqueued": 0, "processed": 0, "failed": 0}
    assert sent == []


def test_run_once_force_runs_before_scan_hour(tmp_path):
    tz = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 6, 24, 9, 0, tzinfo=tz)  # before SCAN_HOUR 22
    chats = [ChatRef("a@s.whatsapp.net", "Alice", datetime(2026, 6, 24, 8, tzinfo=tz))]
    msgs = {"a@s.whatsapp.net": [Message("m", "a@s.whatsapp.net", "a@s.whatsapp.net",
            False, datetime(2026, 6, 24, 8, tzinfo=tz), "hi", "", "", 0)]}
    store = Store(str(tmp_path / "t.db"))
    cfg = Config(settings=_settings(), users=[User("8801", "x@y.com", 22, "m", "m2")])
    sent, alerts = [], []
    deps = _deps(_FakeGowa(chats, msgs), store, sent, alerts)
    stats = run_once(cfg, deps, now, force=True)
    assert stats["enqueued"] == 1 and stats["processed"] == 1
    assert sent and sent[0][0] == "x@y.com"
    # --run-now must NOT mark the daily scan so the scheduled 10pm run still fires
    assert not store.has_scan("2026-06-24", "8801@s.whatsapp.net")


def test_force_does_not_block_scheduled_run(tmp_path):
    """--run-now followed by 10pm tick: scheduled run enqueues again but
    already-done conversations are skipped (INSERT OR IGNORE)."""
    tz = ZoneInfo("Asia/Dhaka")
    chats = [ChatRef("a@s.whatsapp.net", "Alice", datetime(2026, 6, 24, 8, tzinfo=tz))]
    msgs = {"a@s.whatsapp.net": [Message("m", "a@s.whatsapp.net", "a@s.whatsapp.net",
            False, datetime(2026, 6, 24, 8, tzinfo=tz), "hi", "", "", 0)]}
    store = Store(str(tmp_path / "t.db"))
    cfg = Config(settings=_settings(), users=[User("8801", "x@y.com", 22, "m", "m2")])

    # First: --run-now at 9am
    sent, alerts = [], []
    deps = _deps(_FakeGowa(chats, msgs), store, sent, alerts)
    run_once(cfg, deps, datetime(2026, 6, 24, 9, 0, tzinfo=tz), force=True)
    assert len(sent) == 1

    # Second: scheduled tick at 10pm — scan_hour condition now met
    sent2, alerts2 = [], []
    deps2 = _deps(_FakeGowa(chats, msgs), store, sent2, alerts2)
    stats = run_once(cfg, deps2, datetime(2026, 6, 24, 22, 30, tzinfo=tz), force=False)
    # Re-enqueue attempt happens (INSERT OR IGNORE keeps row as 'done')
    # already-done conversations are not re-processed → no new emails
    assert stats["processed"] == 0
    assert sent2 == []


def test_failed_summary_marks_failed_and_alerts(tmp_path):
    tz = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 6, 24, 22, 30, tzinfo=tz)
    chats = [ChatRef("a@s.whatsapp.net", "Alice", datetime(2026, 6, 24, 10, tzinfo=tz))]
    msgs = {"a@s.whatsapp.net": [Message("m", "a@s.whatsapp.net", "a@s.whatsapp.net",
            False, datetime(2026, 6, 24, 10, tzinfo=tz), "hi", "", "", 0)]}
    store = Store(str(tmp_path / "t.db"))

    class _BoomGemini:
        def generate(self, parts, primary, fallback): raise RuntimeError("gemini down")

    sent, alerts = [], []
    deps = Deps(gowa=_FakeGowa(chats, msgs), gemini=_BoomGemini(), store=store,
                mailer_send=lambda *a: sent.append(a), notify=lambda t: alerts.append(t))
    cfg = Config(settings=_settings(), users=[User("8801", "x@y.com", 22, "m", "m2")])
    stats = run_once(cfg, deps, now)
    assert stats["failed"] == 1 and sent == []
    assert alerts
    assert len(store.next_batch("2026-06-24", max_attempts=2)) == 1


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
    settings = _settings(max_email_attach_mb=18, max_total_media_mb=60)
    cfg = Config(settings=settings, users=[user])
    sent, alerts = [], []
    deps = _deps(_MediaGowa(chats, msgs, payloads), store, sent, alerts)
    stats = run_once(cfg, deps, now)
    assert stats["processed"] == 1
    assert len(sent) == 1
    assert sent[0][4] is None or sent[0][4] == []  # nothing attachable
    assert "too large" in sent[0][2].lower()  # named in plain-text body
