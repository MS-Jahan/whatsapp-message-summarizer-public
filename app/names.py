from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# WhatsApp encodes @-mentions in message text as "@<digits>", where the digits
# are either a phone number or a LID (a long opaque id). Require 5+ digits so we
# don't rewrite trivial tokens.
_MENTION = re.compile(r"@(\d{5,})")


@dataclass
class NameResolver:
    """Resolves WhatsApp jids/LIDs to human names for transcript labelling.

    - ``contacts``: ``"<phone>@s.whatsapp.net" -> saved contact name``
    - ``lid_to_phone``: ``"<lid>@lid" -> "<phone>@s.whatsapp.net"`` (group members)
    - ``display_names``: ``"<phone>@s.whatsapp.net" -> group DisplayName``
    """
    contacts: dict[str, str] = field(default_factory=dict)
    lid_to_phone: dict[str, str] = field(default_factory=dict)
    display_names: dict[str, str] = field(default_factory=dict)

    def _name_and_local(self, jid: str) -> tuple[str | None, str]:
        phone = self.lid_to_phone.get(jid, jid)
        local = phone.split("@", 1)[0]
        name = self.contacts.get(phone) or self.display_names.get(phone)
        return name, local

    def name_for_jid(self, jid: str) -> str:
        """Best name for a sender jid; falls back to the bare phone/local id."""
        if not jid:
            return "Unknown"
        name, local = self._name_and_local(jid)
        return name or local

    def rewrite_mentions(self, text: str) -> str:
        """Replace ``@<lid|phone>`` mentions with ``@<name>`` (or ``@<phone>``)."""
        if not text:
            return text

        def repl(m: re.Match) -> str:
            num = m.group(1)
            jid = f"{num}@lid" if f"{num}@lid" in self.lid_to_phone \
                else f"{num}@s.whatsapp.net"
            name, local = self._name_and_local(jid)
            return f"@{name}" if name else f"@{local}"

        return _MENTION.sub(repl, text)

    @classmethod
    def from_gowa(cls, gowa, device: str, chat_jid: str) -> "NameResolver":
        """Build a resolver from GoWA: saved contacts (all chats) plus group
        participant LID/phone/DisplayName mappings (group chats only)."""
        try:
            contacts = dict(gowa.contacts_map(device))
        except Exception as e:  # noqa: BLE001 - name resolution must never fail a summary
            log.warning("contacts lookup failed: %s", e)
            contacts = {}
        lid_to_phone: dict[str, str] = {}
        display_names: dict[str, str] = {}
        if chat_jid.endswith("@g.us"):
            try:
                for p in gowa.group_participants(device, chat_jid):
                    lid = p.get("LID") or p.get("JID")
                    phone = p.get("PhoneNumber") or p.get("JID")
                    if lid and phone:
                        lid_to_phone[lid] = phone
                    display = p.get("DisplayName")
                    if phone and display:
                        display_names[phone] = display
            except Exception as e:  # noqa: BLE001
                log.warning("group participants lookup failed for %s: %s", chat_jid, e)
        return cls(contacts=contacts, lid_to_phone=lid_to_phone,
                   display_names=display_names)
