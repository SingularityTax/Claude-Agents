"""Bitrix24: Projekte, Mitglieder, Projekt-Chats und Live-Feed-Beiträge.

Mandant = externer Benutzer (Extranet) in einem Projekt. Mitarbeiter = interner Benutzer.
Die REST-Methoden und Feldnamen sind gegen das echte Portal mit `sla-monitor bitrix-probe`
zu prüfen, bevor der Rückimport läuft.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime
from typing import Iterator

import requests

from .config import Config, get_secret
from .model import Event

log = logging.getLogger(__name__)
_TAGS = re.compile(r"\[/?[a-zA-Z]+[^\]]*\]|<[^>]+>")


def _clean(text: str) -> str:
    return html.unescape(_TAGS.sub("", text or "")).strip()


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


class BitrixClient:
    def __init__(self, cfg: Config):
        url = get_secret("bitrix-webhook-url")
        self.base = url.rstrip("/") + "/"
        self.min_interval = 1.0 / max(cfg.bitrix_rps, 0.1)
        self._last = 0.0
        self._session = requests.Session()

    def call(self, method: str, params: dict | None = None) -> dict:
        for attempt in range(6):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self._session.post(f"{self.base}{method}.json", json=params or {}, timeout=60)
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if data.get("error") == "QUERY_LIMIT_EXCEEDED" or r.status_code == 503:
                time.sleep(2 ** attempt)
                continue
            if "error" in data:
                raise RuntimeError(f"Bitrix {method}: {data.get('error')}: {data.get('error_description')}")
            r.raise_for_status()
            return data
        raise RuntimeError(f"Bitrix {method}: zu viele Wiederholungen")

    def list_all(self, method: str, params: dict | None = None) -> Iterator[dict]:
        params = dict(params or {})
        start = 0
        while True:
            params["start"] = start
            data = self.call(method, params)
            result = data.get("result") or []
            if isinstance(result, dict):  # manche Methoden liefern {"items": [...]}
                result = result.get("items") or list(result.values())
            yield from result
            nxt = data.get("next")
            if nxt is None:
                return
            start = nxt

    def batch(self, commands: dict[str, str]) -> dict:
        """Bis zu 50 Aufrufe in einer Anfrage."""
        out: dict = {}
        items = list(commands.items())
        for i in range(0, len(items), 50):
            chunk = dict(items[i : i + 50])
            data = self.call("batch", {"halt": 0, "cmd": chunk})
            res = data.get("result") or {}
            out.update(res.get("result") or {})
            for key, err in (res.get("result_error") or {}).items():
                log.warning("Bitrix batch %s: %s", key, err)
        return out


def load_directory(bx: BitrixClient) -> tuple[list[dict], dict[str, dict], dict[str, list[str]]]:
    """Projekte, alle Benutzer (inkl. Extranet) und Mitglieder je Projekt."""
    groups = list(bx.list_all("sonet_group.get", {"ORDER": {"ID": "ASC"}}))
    users: dict[str, dict] = {}
    for u in bx.list_all("user.get", {"FILTER": {"ACTIVE": True}}):
        users[str(u["ID"])] = u
    cmds = {f"g{g['ID']}": f"sonet_group.user.get?ID={g['ID']}" for g in groups}
    members_raw = bx.batch(cmds)
    members = {
        key[1:]: [str(m["USER_ID"]) for m in (val or [])]
        for key, val in members_raw.items()
    }
    return groups, users, members


def is_external(user: dict) -> bool:
    # Extranet-Benutzer haben keine Abteilung; USER_TYPE ist je nach Portal "extranet" oder fehlt.
    if (user.get("USER_TYPE") or "").lower() in ("extranet", "email"):
        return True
    return not user.get("UF_DEPARTMENT")


def client_groups(groups, users, members) -> list[dict]:
    """Nur Projekte mit mindestens einem externen Mitglied."""
    out = []
    for g in groups:
        ids = members.get(str(g["ID"]), [])
        if any(is_external(users.get(uid, {})) for uid in ids):
            out.append(g)
    return out


def owners_from_groups(cfg: Config, groups, users, members) -> dict[str, str]:
    """E-Mail des Mandanten -> E-Mail des Projektverantwortlichen."""
    mapping: dict[str, str] = {}
    for g in groups:
        owner = users.get(str(g.get("OWNER_ID")), {})
        owner_mail = (owner.get("EMAIL") or "").lower()
        if not owner_mail:
            continue
        for uid in members.get(str(g["ID"]), []):
            u = users.get(uid, {})
            mail = (u.get("EMAIL") or "").lower()
            if mail and is_external(u):
                mapping.setdefault(mail, owner_mail)
    return mapping


def _user_email(users: dict[str, dict], uid) -> tuple[str, str]:
    u = users.get(str(uid), {})
    name = f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip()
    return (u.get("EMAIL") or f"bitrix-user-{uid}").lower(), name


def _event(cfg, users, group, uid, author_id, ts, text, kind_hint) -> Event:
    author = users.get(str(author_id), {})
    email, name = _user_email(users, author_id)
    external = is_external(author) if author else True
    ev = Event(
        uid=uid,
        source="bitrix",
        timestamp=ts,
        direction="in" if external else "out",
        sender=email,
        sender_name=name,
        subject=f"[Bitrix] {group.get('NAME', '')} ({kind_hint})",
        body=_clean(text)[:4000],
        mailboxes=[f"bitrix:{group['ID']}"],
        thread_hints=[f"bx:{kind_hint}:{group['ID']}"],
        bitrix_group_id=str(group["ID"]),
    )
    if not external:
        ev.responder = email
        ev.responder_via = "bitrix"
    return ev


def fetch_group_chat(cfg, bx: BitrixClient, users, group, since: datetime) -> Iterator[Event]:
    last_id = None
    while True:
        params = {"DIALOG_ID": f"sg{group['ID']}", "LIMIT": 50}
        if last_id:
            params["LAST_ID"] = last_id
        try:
            res = bx.call("im.dialog.messages.get", params).get("result") or {}
        except RuntimeError as e:
            log.info("Projekt %s: kein Chat (%s)", group["ID"], e)
            return
        msgs = res.get("messages") or []
        if not msgs:
            return
        for m in msgs:
            ts = _ts(m["date"])
            if ts < since:
                return
            if not m.get("author_id"):  # Systemnachricht
                continue
            yield _event(cfg, users, group, f"bx:chat:{m['id']}", m["author_id"], ts, m.get("text", ""), "chat")
        last_id = min(int(m["id"]) for m in msgs)


def fetch_group_feed(cfg, bx: BitrixClient, users, group, since: datetime) -> Iterator[Event]:
    try:
        posts = bx.list_all("log.blogpost.get", {"LOG_RIGHTS": [f"SG{group['ID']}"]})
        for p in posts:
            ts = _ts(p["DATE_PUBLISH"])
            if ts < since:
                continue
            yield _event(cfg, users, group, f"bx:post:{p['ID']}", p["AUTHOR_ID"], ts,
                         f"{p.get('TITLE', '')}\n{p.get('DETAIL_TEXT', '')}", "feed")
    except RuntimeError as e:
        log.info("Projekt %s: kein Feed (%s)", group["ID"], e)
