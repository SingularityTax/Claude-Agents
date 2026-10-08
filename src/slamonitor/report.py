"""Auswertung als HTML-Bericht und CSV. Ausgabe nur lokal im data_dir."""
from __future__ import annotations

import csv
import html
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .config import Config
from .model import Case, Event

STATUS_DE = {
    "answered_in_sla": "beantwortet, in Frist",
    "answered_late": "beantwortet, zu spät",
    "open_in_sla": "offen, in Frist",
    "open_overdue": "offen, ÜBERFÄLLIG",
}
VIA_DE = {"personal": "eigenes Postfach", "audit": "Audit-Log", "signature": "Signatur",
          "bitrix": "Bitrix", "unknown": "unbekannt", "": ""}


def _fmt(dt: datetime | None, cfg: Config) -> str:
    if not dt:
        return ""
    from zoneinfo import ZoneInfo
    return dt.astimezone(ZoneInfo(cfg.sla.timezone)).strftime("%d.%m.%Y %H:%M")


def _h(x) -> str:
    return html.escape(str(x if x is not None else ""))


def staff_stats(cfg: Config, cases: list[Case]) -> list[dict]:
    names = {s.email.lower(): s.name for s in cfg.staff}
    per: dict[str, list[Case]] = defaultdict(list)
    for c in cases:
        if c.responder:
            per[c.responder].append(c)
    owner_open = Counter(c.owner for c in cases if c.owner and c.status.startswith("open"))
    rows = []
    for email in sorted(set(per) | set(names)):
        cs = per.get(email, [])
        hours = [c.response_business_hours for c in cs if c.response_business_hours is not None]
        in_sla = sum(1 for c in cs if c.status == "answered_in_sla")
        rows.append({
            "email": email,
            "name": names.get(email, email),
            "monitored": email in names,
            "answered": len(cs),
            "in_sla": in_sla,
            "sla_rate": (in_sla / len(cs)) if cs else None,
            "median_h": statistics.median(hours) if hours else None,
            "interim": sum(1 for c in cs if c.response_kind == "interim"),
            "open_as_owner": owner_open.get(email, 0),
        })
    rows.sort(key=lambda r: (not r["monitored"], -(r["answered"])))
    return rows


def write_csv(cfg: Config, cases: list[Case], path: Path) -> None:
    fields = ["case_id", "status", "priority", "source", "client", "subject", "opened_at", "due_at",
              "response_at", "response_business_hours", "response_kind", "responder", "responder_via",
              "owner", "followups", "auto_closures", "auto_acks", "mailboxes", "web_link"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(fields)
        for c in cases:
            w.writerow([
                c.case_id, STATUS_DE[c.status], c.priority, c.source, c.client, c.subject,
                _fmt(c.opened_at, cfg), _fmt(c.due_at, cfg), _fmt(c.response_at, cfg),
                f"{c.response_business_hours:.1f}" if c.response_business_hours is not None else "",
                c.response_kind or "", c.responder or "", VIA_DE.get(c.responder_via, c.responder_via),
                c.owner or "", c.followups, c.auto_closures, c.auto_acks, " ".join(c.mailboxes), c.web_link,
            ])


def write_html(cfg: Config, cases: list[Case], events: list[Event], since: datetime,
               now: datetime, path: Path) -> None:
    status = Counter(c.status for c in cases)
    kinds = Counter(e.kind for e in events)
    per_mailbox = Counter(mb for e in events if e.direction == "in" for mb in e.mailboxes)
    answered = [c for c in cases if c.response_at]
    med = statistics.median([c.response_business_hours for c in answered]) if answered else None
    fake = [c for c in cases if c.auto_closures and not c.response_at]
    open_overdue = sorted([c for c in cases if c.status == "open_overdue"], key=lambda c: c.opened_at)
    unknown_resp = sum(1 for c in answered if c.responder_via == "unknown")

    def tile(label, value, warn=False):
        cls = "tile warn" if warn else "tile"
        return f'<div class="{cls}"><div class="v">{_h(value)}</div><div class="l">{_h(label)}</div></div>'

    def case_rows(cs):
        out = []
        for c in cs:
            link = f'<a href="{_h(c.web_link)}">öffnen</a>' if c.web_link else ""
            out.append(
                f"<tr><td>{_h(c.priority)}</td><td>{_h(_fmt(c.opened_at, cfg))}</td><td>{_h(c.client)}</td>"
                f"<td>{_h(c.subject[:90])}</td><td>{_h(', '.join(c.mailboxes))}</td><td>{_h(c.owner or '–')}</td>"
                f"<td>{c.followups}</td><td>{c.auto_closures}</td><td>{link}</td></tr>"
            )
        return "\n".join(out)

    staff_rows = []
    for r in staff_stats(cfg, cases):
        rate = f"{r['sla_rate']:.0%}" if r["sla_rate"] is not None else "–"
        medh = f"{r['median_h']:.1f} h" if r["median_h"] is not None else "–"
        tag = "" if r["monitored"] else " <small>(nicht bewertet)</small>"
        staff_rows.append(
            f"<tr><td>{_h(r['name'])}{tag}</td><td>{r['answered']}</td><td>{r['in_sla']}</td><td>{rate}</td>"
            f"<td>{medh}</td><td>{r['interim']}</td><td>{r['open_as_owner']}</td></tr>"
        )

    head = ["Prio", "Eingang", "Mandant", "Betreff", "Postfach", "Zuständig (Bitrix)", "Nachfragen",
            "Schein-erledigt", ""]
    thead = "".join(f"<th>{h}</th>" for h in head)
    page = f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>SLA-Baseline</title>
<style>
body{{font-family:-apple-system,Segoe UI,sans-serif;margin:24px;color:#1b1f24;background:#fff}}
h1{{font-size:22px}} h2{{font-size:17px;margin-top:32px}}
.tiles{{display:flex;flex-wrap:wrap;gap:12px}} .tile{{border:1px solid #d0d7de;border-radius:8px;padding:12px 16px;min-width:140px}}
.tile .v{{font-size:24px;font-weight:600}} .tile .l{{font-size:12px;color:#57606a}} .warn{{border-color:#cf222e}} .warn .v{{color:#cf222e}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}} th,td{{border-bottom:1px solid #eaeef2;padding:6px 8px;text-align:left;vertical-align:top}}
th{{background:#f6f8fa}} small{{color:#57606a}} .note{{font-size:13px;color:#57606a;max-width:900px}}
</style></head><body>
<h1>Antwortzeiten – Baseline</h1>
<p class="note">Zeitraum {_h(_fmt(since, cfg))} bis {_h(_fmt(now, cfg))}. Frist: {cfg.sla.normal_hours:g} Arbeitsstunden
(1 Werktag), dringend {cfg.sla.urgent_hours:g} Arbeitsstunden. Arbeitszeit {cfg.sla.workday_start}–{cfg.sla.workday_end}, Mo–Fr, Feiertage {cfg.sla.holidays_subdivision}.
Schattenbetrieb: keine Eskalationen. Einstufung {"mit KI" if cfg.llm_enabled else "nur regelbasiert (KI aus)"}.</p>
<div class="tiles">
{tile("Mandantenanfragen", len(cases))}
{tile("beantwortet in Frist", status["answered_in_sla"])}
{tile("beantwortet zu spät", status["answered_late"], status["answered_late"] > 0)}
{tile("offen, überfällig", status["open_overdue"], status["open_overdue"] > 0)}
{tile("offen, in Frist", status["open_in_sla"])}
{tile("Median Erstantwort", f"{med:.1f} h" if med is not None else "–")}
{tile("Schein-erledigt, ohne Antwort", len(fake), len(fake) > 0)}
{tile("Antwortende/r unbekannt", unknown_resp, unknown_resp > 0)}
</div>
<h2>Offen und überfällig ({len(open_overdue)})</h2>
<table><thead><tr>{thead}</tr></thead><tbody>{case_rows(open_overdue)}</tbody></table>
<h2>„Erledigt“ gemeldet ohne echte Antwort ({len(fake)})</h2>
<p class="note">Der alte Ticket-Agent hat „reviewed and addressed“ verschickt, eine inhaltliche Antwort fehlt bis heute.</p>
<table><thead><tr>{thead}</tr></thead><tbody>{case_rows(fake)}</tbody></table>
<h2>Mitarbeiter</h2>
<p class="note">„Beantwortet“ zählt die erste echte Antwort je Anfrage. „Offen als Zuständige/r“ = offene Anfragen von Mandanten,
deren Bitrix-Projekt diese Person verantwortet.</p>
<table><thead><tr><th>Person</th><th>beantwortet</th><th>davon in Frist</th><th>Quote</th><th>Median</th>
<th>nur Zwischenstand</th><th>offen als Zuständige/r</th></tr></thead><tbody>{"".join(staff_rows)}</tbody></table>
<h2>Eingang je Postfach</h2>
<table><thead><tr><th>Postfach / Quelle</th><th>eingehende Nachrichten</th></tr></thead><tbody>
{"".join(f"<tr><td>{_h(k)}</td><td>{v}</td></tr>" for k, v in per_mailbox.most_common())}
</tbody></table>
<h2>Einstufung aller Nachrichten</h2>
<table><tbody>{"".join(f"<tr><td>{_h(k)}</td><td>{v}</td></tr>" for k, v in kinds.most_common())}</tbody></table>
</body></html>"""
    path.write_text(page, encoding="utf-8")
