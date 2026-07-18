from app.selftest import run_selftest, check_gemini
from app.models import Config, Settings, User


def _settings(**over):
    base = dict(
        gowa_base_url="x", gowa_basic_auth=("u", "p"), timezone="Asia/Dhaka",
        scan_hour=22, gemini_primary_model="PRI", gemini_fallback_model="FB",
        gemini_key_free="FREE", gemini_key_paid="PAID",
        max_chat_attempts=5, max_video_mb=10, max_media_items=30, max_total_media_mb=40,
        resend_api_key="", smtp_host="", smtp_port=587, smtp_user="", smtp_pass="",
        smtp_tls=True, mail_from="b@x.com", telegram_bot_token="t", telegram_chat_id="1",
        log_level="INFO", db_path=":x", users_file="u")
    base.update(over)
    return Settings(**base)


def _config(**over):
    user = User("8801", "x@y.com", 22, "PRI", "FB")
    return Config(settings=_settings(**over), users=[user])


class _FakeGowa:
    def __init__(self, chats=3, boom=False):
        self._chats = chats
        self._boom = boom

    def list_chats(self, device):
        if self._boom:
            raise RuntimeError("gowa down")
        return list(range(self._chats))


class _FakeGemini:
    def __init__(self, fail_models=()):
        self.fail_models = set(fail_models)
        self.calls = []

    def _call(self, key, model, parts):
        self.calls.append((key, model))
        if model in self.fail_models:
            raise RuntimeError("model not found")
        return "OK"


def test_all_checks_pass():
    sent = []
    results = run_selftest(
        _config(), _FakeGowa(chats=5), _FakeGemini(),
        send=lambda s, to, subj, body: sent.append((to, subj)))
    assert all(r.ok for r in results)
    names = [r.name for r in results]
    assert names == ["gowa[8801]", "gemini[primary:PRI]", "gemini[fallback:FB]", "email[x@y.com]"]
    assert sent == [("x@y.com", "WhatsApp Summarizer self-test")]


def test_gemini_uses_free_key_first_then_paid():
    g = _FakeGemini(fail_models=())
    check_gemini(_settings(), g)
    # each model probed once, free key first (succeeds, so paid not tried)
    assert g.calls == [("FREE", "PRI"), ("FREE", "FB")]


def test_gemini_falls_back_to_paid_key_when_free_fails():
    class _FreeFails(_FakeGemini):
        def _call(self, key, model, parts):
            self.calls.append((key, model))
            if key == "FREE":
                raise RuntimeError("free quota")
            return "OK"

    g = _FreeFails()
    results = check_gemini(_settings(), g)
    assert all(r.ok for r in results)
    assert g.calls == [("FREE", "PRI"), ("PAID", "PRI"), ("FREE", "FB"), ("PAID", "FB")]


def test_failures_reported_not_raised():
    sent = []

    def boom_send(s, to, subj, body):
        raise RuntimeError("smtp refused")

    results = run_selftest(
        _config(), _FakeGowa(boom=True), _FakeGemini(fail_models={"FB"}),
        send=boom_send)
    by_name = {r.name: r for r in results}
    assert by_name["gowa[8801]"].ok is False
    assert by_name["gemini[primary:PRI]"].ok is True
    assert by_name["gemini[fallback:FB]"].ok is False
    assert by_name["email[x@y.com]"].ok is False


def test_skip_flags_and_email_override():
    sent = []
    results = run_selftest(
        _config(), _FakeGowa(), _FakeGemini(),
        send=lambda s, to, subj, body: sent.append((to, subj)),
        email_to="override@z.com", do_gemini=False, do_email=True)
    names = [r.name for r in results]
    assert "gemini[primary:PRI]" not in names
    assert sent == [("override@z.com", "WhatsApp Summarizer self-test")]
