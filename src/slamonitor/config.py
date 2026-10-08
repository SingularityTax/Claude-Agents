"""Konfiguration und Geheimnisse.

Geheimnisse liegen im macOS-Schlüsselbund (Dienst "sla-monitor"). Für Tests und
Notfälle gibt es Umgebungsvariablen als Fallback.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

KEYRING_SERVICE = "sla-monitor"

SECRET_ENV = {
    "graph-client-secret": "SLA_GRAPH_CLIENT_SECRET",
    "bitrix-webhook-url": "SLA_BITRIX_WEBHOOK_URL",
    "anthropic-api-key": "ANTHROPIC_API_KEY",
}


def get_secret(name: str, required: bool = True) -> str | None:
    env = SECRET_ENV.get(name)
    if env and os.environ.get(env):
        return os.environ[env]
    try:
        import keyring

        value = keyring.get_password(KEYRING_SERVICE, name)
    except Exception:  # kein Keyring-Backend verfügbar
        value = None
    if not value and required:
        raise RuntimeError(
            f'Geheimnis "{name}" fehlt. Anlegen mit: '
            f'python -m keyring set {KEYRING_SERVICE} {name}'
        )
    return value


@dataclass
class Staff:
    email: str
    name: str
    signature_names: list[str] = field(default_factory=list)
    read_sent_items: bool = True


@dataclass
class SlaSettings:
    timezone: str = "Europe/Berlin"
    workday_start: str = "08:00"
    workday_end: str = "18:00"
    holidays_subdivision: str = "NW"
    normal_hours: float = 10
    urgent_hours: float = 4


@dataclass
class Config:
    tenant_id: str
    client_id: str
    data_dir: Path
    internal_domains: list[str]
    shared_mailboxes: list[str]
    staff: list[Staff]
    unmonitored_responders: list[str]
    noise_senders: list[str]
    noise_domains: list[str]
    sla: SlaSettings
    auto_ack_patterns: list[str]
    bitrix_enabled: bool = True
    bitrix_rps: float = 2
    llm_enabled: bool = False
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "low"
    report_language: str = "de"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "monitor.sqlite"

    def is_internal(self, address: str | None) -> bool:
        if not address:
            return False
        domain = address.rsplit("@", 1)[-1].lower()
        return domain in self.internal_domains

    def staff_by_email(self) -> dict[str, Staff]:
        return {s.email.lower(): s for s in self.staff}


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sla = SlaSettings(**(raw.get("sla") or {}))
    bitrix = raw.get("bitrix") or {}
    llm = raw.get("llm") or {}
    report = raw.get("report") or {}
    data_dir = Path(os.path.expanduser(raw.get("data_dir", "~/sla-monitor-data")))
    data_dir.mkdir(parents=True, exist_ok=True)
    return Config(
        tenant_id=raw["tenant_id"],
        client_id=raw["client_id"],
        data_dir=data_dir,
        internal_domains=[d.lower() for d in raw.get("internal_domains", [])],
        shared_mailboxes=[m.lower() for m in raw.get("shared_mailboxes", [])],
        staff=[Staff(**s) for s in raw.get("staff", [])],
        unmonitored_responders=[m.lower() for m in raw.get("unmonitored_responders", [])],
        noise_senders=[m.lower() for m in raw.get("noise_senders", [])],
        noise_domains=[d.lower() for d in raw.get("noise_domains", [])],
        sla=sla,
        auto_ack_patterns=raw.get("auto_ack_patterns", []),
        bitrix_enabled=bitrix.get("enabled", True),
        bitrix_rps=float(bitrix.get("requests_per_second", 2)),
        llm_enabled=llm.get("enabled", False),
        llm_model=llm.get("model", "claude-opus-5-5"),
        llm_effort=llm.get("effort", "low"),
        report_language=report.get("language", "de"),
    )
