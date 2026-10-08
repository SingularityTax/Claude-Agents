"""Thread-Bildung, Zuordnung der Antwortenden und Fall-/SLA-Berechnung."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime

from .config import Config
from .hours import BusinessClock
from .model import Case, Event

_SUBJECT_PREFIX = re.compile(r"^\s*((re|aw|fw|fwd|wg|tr|rif|r|sv|vs)\s*(\[\d+\])?\s*:\s*)+", re.I)
_SNG = re.compile(r"SNG-\d{8}-[0-9A-F]{6}", re.I)


def normalize_subject(subject: str) -> str:
    s = _SUBJECT_PREFIX.sub("", subject or "")
    s = _SNG.sub("", s)
    s = re.sub(r"\b(reference|urgent)\s*:?", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" -–:").lower()


def sng_refs(text: str) -> set[str]:
    return {m.upper() for m in _SNG.findall(text or "")}


def external_parties(cfg: Config, ev: Event) -> list[str]:
    if ev.direction == "in":
        return [ev.sender.lower()] if ev.sender else []
    return [r.lower() for r in ev.recipients if not cfg.is_internal(r)]


def add_derived_hints(cfg: Config, ev: Event) -> None:
    """Ergänzt Betreffs- und Referenz-Schlüssel (Quell-Adapter liefern Header-Schlüssel)."""
    if ev.source != "mail":
        return
    for ref in sng_refs(f"{ev.subject}\n{ev.body}"):
        ev.thread_hints.append(f"sng:{ref}")
    ns = normalize_subject(ev.subject)
    if len(ns) >= 4:
        for party in external_parties(cfg, ev):
            ev.thread_hints.append(f"subj:{ns}|{party}")


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_threads(events: list[Event]) -> dict[str, list[Event]]:
    uf = _UnionFind()
    for ev in events:
        node = f"ev:{ev.uid}"
        uf.find(node)
        for hint in ev.thread_hints:
            uf.union(node, hint)
    threads: dict[str, list[Event]] = defaultdict(list)
    for ev in events:
        root = uf.find(f"ev:{ev.uid}")
        tid = hashlib.sha1(root.encode()).hexdigest()[:12]
        threads[tid].append(ev)
    for evs in threads.values():
        evs.sort(key=lambda e: e.timestamp)
    return threads


_QUOTE_MARKERS = re.compile(
    r"(\n-{2,}\s*Original|\nFrom:\s|\nVon:\s|\nOn .{5,80} wrote:|\nAm .{5,80} schrieb|"
    r"\nGesendet:\s|\nSent:\s|\nMit freundlichen Grüßen / Kind regards)",
    re.I,
)


def _signature_zone(body: str) -> str:
    """Der Text vor dem ersten Zitat, davon das Ende: dort steht der Gruß mit Namen."""
    m = _QUOTE_MARKERS.search("\n" + body)
    head = body[: m.start()] if m else body
    return head[-500:]


def attribute_responder(cfg: Config, ev: Event, audit_map: dict[str, str]) -> None:
    """Ermittelt, welche Person eine ausgehende Nachricht geschrieben hat."""
    if ev.direction != "out" or ev.source != "mail":
        return
    sender = ev.sender.lower()
    staff = cfg.staff_by_email()
    if sender in staff or sender in cfg.unmonitored_responders:
        ev.responder, ev.responder_via = sender, "personal"
        return
    if ev.uid in audit_map:
        ev.responder, ev.responder_via = audit_map[ev.uid].lower(), "audit"
        return
    # Als Sammelpostfach gesendet, aber die Kopie liegt auch in den Gesendeten Elementen einer Person
    for mb in ev.mailboxes:
        if mb.lower() in staff:
            ev.responder, ev.responder_via = mb.lower(), "personal"
            return
    zone = _signature_zone(ev.body).lower()
    for s in cfg.staff:
        for name in s.signature_names or [s.name]:
            if name and re.search(r"\b" + re.escape(name.lower()) + r"\b", zone):
                ev.responder, ev.responder_via = s.email.lower(), "signature"
                return
    ev.responder, ev.responder_via = None, "unknown"


def build_cases(
    cfg: Config,
    clock: BusinessClock,
    threads: dict[str, list[Event]],
    now: datetime,
    owners: dict[str, str] | None = None,
) -> list[Case]:
    owners = owners or {}
    cases: list[Case] = []
    for tid, evs in threads.items():
        open_case: Case | None = None
        n = 0
        for ev in evs:
            if ev.kind == "client_request":
                if open_case is None:
                    n += 1
                    hours = cfg.sla.urgent_hours if ev.priority == "P1" else cfg.sla.normal_hours
                    open_case = Case(
                        case_id=f"{tid}-{n}",
                        thread_id=tid,
                        source=ev.source,
                        client=ev.sender.lower(),
                        subject=ev.subject,
                        opened_at=ev.timestamp,
                        priority=ev.priority,
                        due_at=clock.add_business_hours(ev.timestamp, hours),
                        mailboxes=list(ev.mailboxes),
                        first_request_uid=ev.uid,
                        web_link=ev.web_link,
                        owner=owners.get(ev.sender.lower()),
                    )
                else:
                    open_case.followups += 1
                    for mb in ev.mailboxes:
                        if mb not in open_case.mailboxes:
                            open_case.mailboxes.append(mb)
                    if ev.priority == "P1" and open_case.priority != "P1":
                        open_case.priority = "P1"
                        open_case.due_at = min(
                            open_case.due_at,
                            clock.add_business_hours(open_case.opened_at, cfg.sla.urgent_hours),
                        )
            elif open_case is not None and ev.direction == "out":
                if ev.kind == "auto_closure":
                    open_case.auto_closures += 1
                elif ev.kind == "auto_ack":
                    open_case.auto_acks += 1
                elif ev.kind in ("reply", "interim"):
                    open_case.response_at = ev.timestamp
                    open_case.response_uid = ev.uid
                    open_case.response_kind = ev.kind
                    open_case.responder = ev.responder
                    open_case.responder_via = ev.responder_via
                    open_case.response_business_hours = clock.business_hours_between(
                        open_case.opened_at, ev.timestamp
                    )
                    cases.append(_finalize(open_case, now))
                    open_case = None
        if open_case is not None:
            cases.append(_finalize(open_case, now))
    cases.sort(key=lambda c: c.opened_at)
    return cases


def _finalize(case: Case, now: datetime) -> Case:
    if case.response_at is not None:
        case.status = "answered_in_sla" if case.response_at <= case.due_at else "answered_late"
    else:
        case.status = "open_overdue" if now > case.due_at else "open_in_sla"
    return case
