"""Microsoft Graph: Nachrichten aus Sammelpostfächern und Gesendeten Elementen lesen."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Iterator

import msal
import requests

from .config import Config, get_secret
from .model import Event

log = logging.getLogger(__name__)
GRAPH = "https://graph.microsoft.com/v1.0"

SELECT = ",".join([
    "id", "internetMessageId", "conversationId", "subject", "from", "sender",
    "toRecipients", "ccRecipients", "receivedDateTime", "sentDateTime",
    "uniqueBody", "parentFolderId", "internetMessageHeaders", "webLink", "isDraft",
])
BODY_LIMIT = 4000
_MSGID = re.compile(r"<[^<>\s]+>")


class GraphClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._app = msal.ConfidentialClientApplication(
            cfg.client_id,
            authority=f"https://login.microsoftonline.com/{cfg.tenant_id}",
            client_credential=get_secret("graph-client-secret"),
        )
        self._session = requests.Session()

    def _token(self) -> str:
        result = self._app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            raise RuntimeError(f"Token-Fehler: {result.get('error')}: {result.get('error_description')}")
        return result["access_token"]

    def get(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(6):
            r = self._session.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {self._token()}",
                    "Prefer": 'outlook.body-content-type="text"',
                },
                timeout=60,
            )
            if r.status_code in (429, 503, 504):
                wait = int(r.headers.get("Retry-After", 2 ** attempt))
                log.warning("Graph %s, warte %ss", r.status_code, wait)
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Graph {r.status_code} für {url}: {r.text[:300]}")
            return r.json()
        raise RuntimeError(f"Graph: zu viele Wiederholungen für {url}")

    def paged(self, url: str, params: dict | None = None) -> Iterator[dict]:
        data = self.get(url, params)
        while True:
            yield from data.get("value", [])
            nxt = data.get("@odata.nextLink")
            if not nxt:
                return
            data = self.get(nxt)

    def folder_id(self, mailbox: str, well_known: str) -> str | None:
        try:
            return self.get(f"{GRAPH}/users/{mailbox}/mailFolders/{well_known}")["id"]
        except RuntimeError:
            return None

    def messages(self, mailbox: str, since: datetime, folder: str | None = None) -> Iterator[dict]:
        base = f"{GRAPH}/users/{mailbox}"
        url = f"{base}/mailFolders/{folder}/messages" if folder else f"{base}/messages"
        iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "$select": SELECT,
            "$filter": f"receivedDateTime ge {iso}",
            "$orderby": "receivedDateTime asc",
            "$top": "100",
        }
        try:
            yield from self.paged(url, params)
        except RuntimeError as e:
            # Falls Graph uniqueBody in Listenabfragen ablehnt: auf body ausweichen (enthält dann auch Zitate)
            if "uniqueBody" not in str(e):
                raise
            log.warning("%s: uniqueBody nicht verfügbar, nutze body", mailbox)
            params["$select"] = SELECT.replace("uniqueBody", "body")
            yield from self.paged(url, params)


def _addr(obj: dict | None) -> tuple[str, str]:
    ea = (obj or {}).get("emailAddress") or {}
    return (ea.get("address") or "").lower(), ea.get("name") or ""


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def to_event(cfg: Config, mailbox: str, msg: dict) -> Event | None:
    if msg.get("isDraft"):
        return None
    uid = (msg.get("internetMessageId") or "").strip()
    if not uid:
        return None
    sender, sender_name = _addr(msg.get("from") or msg.get("sender"))
    recipients = [_addr(r)[0] for r in (msg.get("toRecipients") or []) + (msg.get("ccRecipients") or [])]
    recipients = [r for r in recipients if r]
    headers = {h["name"]: h["value"] for h in (msg.get("internetMessageHeaders") or [])}

    if cfg.is_internal(sender):
        direction = "out" if any(not cfg.is_internal(r) for r in recipients) else "internal"
        ts = _ts(msg.get("sentDateTime") or msg["receivedDateTime"])
    else:
        direction = "in"
        ts = _ts(msg["receivedDateTime"])

    hints = [f"mid:{uid}"]
    for key in ("In-Reply-To", "References"):
        for mid in _MSGID.findall(headers.get(key, "")):
            hints.append(f"mid:{mid}")
    if msg.get("conversationId"):
        hints.append(f"conv:{mailbox}:{msg['conversationId']}")

    body = ((msg.get("uniqueBody") or msg.get("body") or {}).get("content") or "")[:BODY_LIMIT]
    return Event(
        uid=uid,
        source="mail",
        timestamp=ts,
        direction=direction,
        sender=sender,
        sender_name=sender_name,
        recipients=recipients,
        subject=msg.get("subject") or "",
        body=body,
        mailboxes=[mailbox],
        thread_hints=hints,
        headers=headers,
        web_link=msg.get("webLink") or "",
    )


def fetch_mail_events(cfg: Config, client: GraphClient, since: datetime) -> Iterator[Event]:
    """Alle Nachrichten der Sammelpostfächer plus die Gesendeten Elemente der Mitarbeiter."""
    for mb in cfg.shared_mailboxes:
        junk = client.folder_id(mb, "junkemail")
        drafts = client.folder_id(mb, "drafts")
        n = 0
        for msg in client.messages(mb, since):
            if msg.get("parentFolderId") in (junk, drafts):
                continue
            ev = to_event(cfg, mb, msg)
            if ev:
                n += 1
                yield ev
        log.info("%s: %d Nachrichten", mb, n)
    for s in cfg.staff:
        if not s.read_sent_items:
            continue
        n = 0
        for msg in client.messages(s.email, since, folder="sentitems"):
            ev = to_event(cfg, s.email.lower(), msg)
            if ev and ev.direction == "out":   # nur Mails nach extern
                n += 1
                yield ev
        log.info("%s (Gesendet): %d externe Nachrichten", s.email, n)
