"""Einheitliches Ereignismodell für E-Mail und Bitrix."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Event:
    """Eine Nachricht aus irgendeiner Quelle.

    uid:          global eindeutig (E-Mail: internetMessageId, Bitrix: "bx:<quelle>:<id>")
    source:       "mail" | "bitrix"
    direction:    "in"  = von außen (Mandant/Dritter)
                  "out" = von uns an mindestens einen externen Empfänger
                  "internal" = nur intern
    """

    uid: str
    source: str
    timestamp: datetime
    direction: str
    sender: str                      # Adresse bzw. Bitrix-Benutzerkennung
    sender_name: str = ""
    recipients: list[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""                   # nur der neue Teil (ohne Zitat), gekürzt
    mailboxes: list[str] = field(default_factory=list)   # wo die Nachricht gefunden wurde
    thread_hints: list[str] = field(default_factory=list)  # Schlüssel für die Thread-Bildung
    headers: dict[str, str] = field(default_factory=dict)
    web_link: str = ""
    bitrix_group_id: str | None = None

    # wird von classify/attribution befüllt
    kind: str = ""                   # client_request | noise | thanks | auto_ack | auto_closure | reply | interim | internal
    priority: str = "P2"
    responder: str | None = None     # Mitarbeiter-E-Mail bei ausgehenden Nachrichten
    responder_via: str = ""          # personal | audit | signature | bitrix | unknown


@dataclass
class Case:
    case_id: str
    thread_id: str
    source: str
    client: str
    subject: str
    opened_at: datetime
    priority: str
    due_at: datetime
    mailboxes: list[str]
    first_request_uid: str
    web_link: str = ""
    followups: int = 0
    response_at: datetime | None = None
    response_uid: str | None = None
    response_kind: str | None = None        # reply | interim
    responder: str | None = None
    responder_via: str = ""
    response_business_hours: float | None = None
    auto_closures: int = 0                  # "reviewed and addressed" ohne echte Antwort
    auto_acks: int = 0
    owner: str | None = None                # Zuständiger laut Bitrix
    status: str = ""                        # answered_in_sla | answered_late | open_in_sla | open_overdue
