"""Import der SendAs-/SendOnBehalf-Einträge aus dem Unified Audit Log.

Phase 1 nutzt einen CSV-Export aus Exchange Online PowerShell
(scripts/export_sendas_audit.ps1), weil die Management Activity API nur die
letzten 7 Tage liefert. Für den laufenden Betrieb (Phase 2+) kommt die API dazu.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


def _find_key(obj, key: str):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == key.lower() and isinstance(v, str):
                return v
            found = _find_key(v, key)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found:
                return found
    return None


def parse_audit_csv(path: Path) -> list[tuple[str, str, str, str]]:
    """Liefert (internetMessageId, Benutzer, Postfach, Zeitpunkt)."""
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for rec in csv.DictReader(f):
            try:
                data = json.loads(rec.get("AuditData") or "{}")
            except json.JSONDecodeError:
                continue
            if data.get("Operation") not in ("SendAs", "SendOnBehalf"):
                continue
            mid = _find_key(data, "InternetMessageId")
            user = data.get("UserId") or rec.get("UserIds") or ""
            if not mid or not user:
                continue
            rows.append((mid.strip(), user.lower(), (data.get("MailboxOwnerUPN") or "").lower(),
                         data.get("CreationTime") or rec.get("CreationDate") or ""))
    return rows
