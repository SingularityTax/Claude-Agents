"""Kommandozeile: sla-monitor <befehl>."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config, get_secret, load_config
from .store import Store

log = logging.getLogger("slamonitor")


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def cmd_check(cfg: Config, args) -> int:
    ok = True
    for name in ("graph-client-secret", "bitrix-webhook-url"):
        present = bool(get_secret(name, required=False))
        print(f"Geheimnis {name}: {'vorhanden' if present else 'FEHLT'}")
        ok &= present
    print(f"KI-Einstufung: {'an' if cfg.llm_enabled else 'aus'}")
    if cfg.llm_enabled:
        print(f"Geheimnis anthropic-api-key: {'vorhanden' if get_secret('anthropic-api-key', required=False) else 'nicht gesetzt (ant-Profil wird versucht)'}")
    if not ok:
        return 1
    from .graph import GRAPH, GraphClient

    g = GraphClient(cfg)
    for mb in cfg.shared_mailboxes + [s.email for s in cfg.staff if s.read_sent_items]:
        try:
            g.get(f"{GRAPH}/users/{mb}/mailFolders/inbox", {"$select": "totalItemCount"})
            print(f"Graph {mb}: Zugriff OK")
        except RuntimeError as e:
            ok = False
            print(f"Graph {mb}: FEHLER {str(e)[:160]}")
    if cfg.bitrix_enabled:
        from .bitrix import BitrixClient

        bx = BitrixClient(cfg)
        total = bx.call("sonet_group.get", {}).get("total")
        print(f"Bitrix: {total} Projekte sichtbar")
    return 0 if ok else 1


def cmd_probe_graph(cfg: Config, args) -> int:
    """Prüft, ob die benötigten Felder geliefert werden. Gibt keine Inhalte aus."""
    from .graph import GraphClient

    g = GraphClient(cfg)
    for mb in cfg.shared_mailboxes:
        msg = next(iter(g.messages(mb, _since(3))), None)
        if not msg:
            print(f"{mb}: keine Nachricht in 3 Tagen")
            continue
        fields = {k: bool(msg.get(k)) for k in ("internetMessageId", "conversationId", "uniqueBody",
                                                "internetMessageHeaders", "webLink")}
        print(f"{mb}: " + ", ".join(f"{k}={'ja' if v else 'NEIN'}" for k, v in fields.items()))
    return 0


def cmd_probe_bitrix(cfg: Config, args) -> int:
    from .bitrix import BitrixClient, client_groups, fetch_group_chat, fetch_group_feed, load_directory

    bx = BitrixClient(cfg)
    groups, users, members = load_directory(bx)
    cgroups = client_groups(groups, users, members)
    externals = sum(1 for u in users.values() if not u.get("UF_DEPARTMENT"))
    print(f"Projekte: {len(groups)}, davon mit externen Mitgliedern: {len(cgroups)}")
    print(f"Benutzer: {len(users)}, davon ohne Abteilung (extern): {externals}")
    print(f"Projekte ohne Verantwortlichen: {sum(1 for g in cgroups if not g.get('OWNER_ID'))}")
    since = _since(args.days)
    for g in cgroups[: args.groups]:
        chat = list(fetch_group_chat(cfg, bx, users, g, since))
        feed = list(fetch_group_feed(cfg, bx, users, g, since))
        print(f"Projekt {g['ID']}: Chat {len(chat)} (Mandant {sum(e.direction == 'in' for e in chat)}), "
              f"Feed {len(feed)} (Mandant {sum(e.direction == 'in' for e in feed)})")
    return 0


def cmd_fetch_mail(cfg: Config, args) -> int:
    from .graph import GraphClient, fetch_mail_events

    store = Store(cfg.db_path)
    n = 0
    for ev in fetch_mail_events(cfg, GraphClient(cfg), _since(args.days)):
        store.upsert_event(ev)
        n += 1
        if n % 500 == 0:
            store.db.commit()
    store.close()
    print(f"{n} E-Mails gespeichert")
    return 0


def cmd_fetch_bitrix(cfg: Config, args) -> int:
    from .bitrix import (BitrixClient, client_groups, fetch_group_chat, fetch_group_feed,
                         load_directory, owners_from_groups)

    bx = BitrixClient(cfg)
    groups, users, members = load_directory(bx)
    cgroups = client_groups(groups, users, members)
    store = Store(cfg.db_path)
    owners = owners_from_groups(cfg, cgroups, users, members)
    store.set_owners(owners, "bitrix")
    since = _since(args.days)
    n = 0
    for i, g in enumerate(cgroups, 1):
        for ev in [*fetch_group_chat(cfg, bx, users, g, since), *fetch_group_feed(cfg, bx, users, g, since)]:
            store.upsert_event(ev)
            n += 1
        if i % 20 == 0:
            store.db.commit()
            log.info("Bitrix: %d/%d Projekte", i, len(cgroups))
    store.close()
    print(f"{n} Bitrix-Nachrichten aus {len(cgroups)} Mandantenprojekten, {len(owners)} Mandant→Zuständig-Zuordnungen")
    return 0


def cmd_import_audit(cfg: Config, args) -> int:
    from .audit import parse_audit_csv

    rows = parse_audit_csv(Path(args.csv).expanduser())
    store = Store(cfg.db_path)
    store.add_audit(rows)
    store.close()
    print(f"{len(rows)} SendAs/SendOnBehalf-Einträge importiert")
    return 0


def cmd_build(cfg: Config, args) -> int:
    from .cases import add_derived_hints, attribute_responder, build_cases, build_threads
    from .classify import classify_event
    from .hours import BusinessClock
    from .report import write_csv, write_html

    store = Store(cfg.db_path)
    since = _since(args.days)
    now = datetime.now(timezone.utc)
    events = store.events(since)
    audit = store.audit_map()
    classifier = None
    if cfg.llm_enabled:
        from .llm import Classifier

        classifier = Classifier(cfg, store)
    for ev in events:
        add_derived_hints(cfg, ev)
        classify_event(cfg, ev)
        if classifier:
            classifier.refine(ev)
        attribute_responder(cfg, ev, audit)
    clock = BusinessClock(cfg.sla)
    cases = build_cases(cfg, clock, build_threads(events), now, store.owners())
    store.save_cases(cases)
    store.close()
    out = cfg.data_dir / "reports"
    out.mkdir(exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%M")
    html_path, csv_path = out / f"baseline-{stamp}.html", out / f"{'cases' if cfg.report_language == 'en' else 'faelle'}-{stamp}.csv"
    write_html(cfg, cases, events, since, now, html_path)
    write_csv(cfg, cases, csv_path)
    print(f"{len(events)} Nachrichten, {len(cases)} Mandantenanfragen")
    print(f"Bericht: {html_path}\nFälle:   {csv_path}")
    return 0


def cmd_run(cfg: Config, args) -> int:
    rc = cmd_fetch_mail(cfg, args)
    if cfg.bitrix_enabled:
        rc |= cmd_fetch_bitrix(cfg, args)
    return rc | cmd_build(cfg, args)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sla-monitor")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="Konfiguration, Geheimnisse und Zugriffe prüfen")
    sub.add_parser("probe-graph", help="Prüfen, ob Graph alle Felder liefert (ohne Inhalte)")
    pb = sub.add_parser("probe-bitrix", help="Bitrix-Struktur prüfen (ohne Inhalte)")
    pb.add_argument("--days", type=int, default=30)
    pb.add_argument("--groups", type=int, default=3)
    for name in ("fetch-mail", "fetch-bitrix", "build", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--days", type=int, default=30)
    ia = sub.add_parser("import-audit", help="CSV aus scripts/export_sendas_audit.ps1 importieren")
    ia.add_argument("csv")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    handlers = {
        "check": cmd_check, "probe-graph": cmd_probe_graph, "probe-bitrix": cmd_probe_bitrix,
        "fetch-mail": cmd_fetch_mail, "fetch-bitrix": cmd_fetch_bitrix, "import-audit": cmd_import_audit,
        "build": cmd_build, "run": cmd_run,
    }
    return handlers[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
