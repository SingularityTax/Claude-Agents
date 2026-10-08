"""Regelbasierte Einstufung von Nachrichten.

Die Regeln sind bewusst konservativ: Im Zweifel gilt eine eingehende Nachricht als
Mandantenanfrage. Lieber eine Anfrage zu viel im Dashboard als eine übersehene.
Die optionale KI-Einstufung (llm.py) kann das später verfeinern.
"""
from __future__ import annotations

import re

from .config import Config
from .model import Event

NOISE_LOCALPARTS = re.compile(
    r"^(no-?reply|do-?not-?reply|noreply|mailer-daemon|postmaster|notifications?|"
    r"newsletter|news|marketing|bounce[s]?|alerts?|info-noreply)\b",
    re.I,
)
NOISE_DOMAINS_BUILTIN = (
    "bitrix24.com", "klaviyomail.com", "mailchimp.com", "sendgrid.net",
    "amazonses.com", "hubspotemail.net", "linkedin.com",
)
AUTO_REPLY_SUBJECT = re.compile(
    r"^(automatic reply|auto(matische)? ?(antwort|reply)|abwesenheit|out of (the )?office|"
    r"undeliverable|unzustellbar|delivery status notification|réponse automatique)",
    re.I,
)
THANKS_ONLY = re.compile(
    r"^\W*(many |vielen |herzlichen |thank(s| you)( so much| very much)?|danke( schön| sehr)?|"
    r"merci|grazie|gracias|ok(ay)?|perfect|perfekt|great|super|received|erhalten)\b",
    re.I,
)
URGENT = re.compile(
    r"(pfändung|garnish|kontosperre|account (has been |was |is )?(blocked|suspended|frozen)|"
    # "Finanzamt" oder "Frist" allein sind in einer Steuerkanzlei Alltag und taugen nicht als P1-Signal.
    # Fristnähe beurteilt erst die KI-Einstufung.
    r"vollstreckung|enforcement|mahnung|säumnis|urgent|dringend|beschwerde|complaint|"
    r"no response|keine (rück)?meldung|not (been )?able to reach|nicht erreich|"
    r"kündig|terminate|cancel (our|the) (contract|service))",
    re.I,
)
INTERIM = re.compile(
    r"(we will (get back|revert|come back|look into|check)|we('re| are) (looking|checking)|"
    r"will be in touch|i will (check|revert|get back|come back)|"
    r"wir (melden uns|kümmern uns|prüfen|schauen uns)|ich (melde mich|prüfe|kümmere mich))",
    re.I,
)


def _localpart_domain(address: str) -> tuple[str, str]:
    local, _, domain = address.lower().partition("@")
    return local, domain


def is_noise_sender(cfg: Config, address: str, headers: dict[str, str]) -> bool:
    address = (address or "").lower()
    if address in cfg.noise_senders:
        return True
    local, domain = _localpart_domain(address)
    if any(domain == d or domain.endswith("." + d) for d in (*NOISE_DOMAINS_BUILTIN, *cfg.noise_domains)):
        return True
    if NOISE_LOCALPARTS.match(local):
        return True
    h = {k.lower(): v.lower() for k, v in headers.items()}
    if h.get("auto-submitted", "no") not in ("", "no"):
        return True
    if "list-unsubscribe" in h or h.get("precedence") in ("bulk", "list", "junk"):
        return True
    return False


def is_auto_ack(cfg: Config, ev: Event) -> bool:
    text = f"{ev.subject}\n{ev.body}"
    return any(p.lower() in text.lower() for p in cfg.auto_ack_patterns)


def is_auto_closure(ev: Event) -> bool:
    return "reviewed and addressed by our team" in ev.body.lower()


def classify_event(cfg: Config, ev: Event) -> None:
    """Setzt ev.kind und ev.priority."""
    text = f"{ev.subject}\n{ev.body}"
    if ev.direction == "internal":
        ev.kind = "internal"
        return
    if ev.direction == "out":
        if is_auto_ack(cfg, ev):
            ev.kind = "auto_closure" if is_auto_closure(ev) else "auto_ack"
        elif AUTO_REPLY_SUBJECT.match(ev.subject or ""):
            ev.kind = "auto_ack"
        elif INTERIM.search(ev.body) and len(ev.body.strip()) < 400:
            ev.kind = "interim"
        else:
            ev.kind = "reply"
        return
    # eingehend
    if ev.source == "mail" and (
        is_noise_sender(cfg, ev.sender, ev.headers) or AUTO_REPLY_SUBJECT.match(ev.subject or "")
    ):
        ev.kind = "noise"
        return
    body = ev.body.strip()
    if body and len(body) < 300 and "?" not in body and THANKS_ONLY.match(body):
        ev.kind = "thanks"
        return
    ev.kind = "client_request"
    ev.priority = "P1" if URGENT.search(text) else "P2"
