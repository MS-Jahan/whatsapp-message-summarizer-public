from app.names import NameResolver


def test_name_for_jid_uses_contact_then_falls_back_to_phone():
    r = NameResolver(contacts={"8801@s.whatsapp.net": "Aminul"})
    assert r.name_for_jid("8801@s.whatsapp.net") == "Aminul"
    assert r.name_for_jid("8802@s.whatsapp.net") == "8802"  # unknown -> local part
    assert r.name_for_jid("") == "Unknown"


def test_name_for_lid_maps_to_phone_then_contact():
    r = NameResolver(
        contacts={"8801700000003@s.whatsapp.net": "Sara"},
        lid_to_phone={"140557821153343@lid": "8801700000003@s.whatsapp.net"})
    assert r.name_for_jid("140557821153343@lid") == "Sara"


def test_display_name_used_when_no_contact():
    r = NameResolver(display_names={"8801@s.whatsapp.net": "Group Display"})
    assert r.name_for_jid("8801@s.whatsapp.net") == "Group Display"


def test_rewrite_mentions_lid_to_name():
    r = NameResolver(
        contacts={"8801700000003@s.whatsapp.net": "Sara"},
        lid_to_phone={"140557821153343@lid": "8801700000003@s.whatsapp.net"})
    assert r.rewrite_mentions("hey @140557821153343 ok") == "hey @Sara ok"


def test_rewrite_mentions_phone_unknown_keeps_number():
    r = NameResolver()
    assert r.rewrite_mentions("@8801700000099 hi") == "@8801700000099 hi"


def test_rewrite_mentions_phone_to_contact():
    r = NameResolver(contacts={"8801700000002@s.whatsapp.net": "Aminul"})
    assert r.rewrite_mentions("@8801700000002 hello") == "@Aminul hello"


def test_rewrite_mentions_handles_empty():
    assert NameResolver().rewrite_mentions("") == ""
    assert NameResolver().rewrite_mentions(None) is None


class _FakeGowa:
    def contacts_map(self, device):
        return {"8801@s.whatsapp.net": "Aminul"}

    def group_participants(self, device, jid):
        return [{"LID": "140557821153343@lid",
                 "PhoneNumber": "8801700000003@s.whatsapp.net",
                 "DisplayName": "Sara G"}]


def test_from_gowa_group_builds_lid_and_display_maps():
    r = NameResolver.from_gowa(_FakeGowa(), "dev@s.whatsapp.net", "g@g.us")
    assert r.contacts == {"8801@s.whatsapp.net": "Aminul"}
    assert r.lid_to_phone == {"140557821153343@lid": "8801700000003@s.whatsapp.net"}
    assert r.name_for_jid("140557821153343@lid") == "Sara G"  # display name fallback


def test_from_gowa_one_to_one_skips_group_lookup():
    r = NameResolver.from_gowa(_FakeGowa(), "dev@s.whatsapp.net", "8801@s.whatsapp.net")
    assert r.lid_to_phone == {}
    assert r.contacts == {"8801@s.whatsapp.net": "Aminul"}
