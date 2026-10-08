from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from slamonitor.config import Config, SlaSettings, Staff
from slamonitor.model import Event

BER = ZoneInfo("Europe/Berlin")


@pytest.fixture
def cfg(tmp_path):
    return Config(
        tenant_id="t",
        client_id="c",
        data_dir=tmp_path,
        internal_domains=["singularity.tax"],
        shared_mailboxes=["filing@singularity.tax", "welcome@singularity.tax", "hello@singularity.tax"],
        staff=[
            Staff(email="anna.test@singularity.tax", name="Anna Test", signature_names=["Anna T"]),
            Staff(email="ben.test@singularity.tax", name="Ben Test", read_sent_items=False),
        ],
        unmonitored_responders=["marko.kaiser@singularity.tax"],
        noise_senders=["recruiter@agency.example"],
        noise_domains=[],
        sla=SlaSettings(),
        auto_ack_patterns=["Reference: SNG-", "We confirm receipt of your enquiry",
                           "has been reviewed and addressed by our team"],
    )


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """Oktober 2026 in Berliner Zeit. 5.10. = Montag, 3.10. = Feiertag (Samstag)."""
    return datetime(2026, 10, day, hour, minute, tzinfo=BER)


def mk(uid, ts, direction, sender, recipients=(), subject="VAT return", body="", mailboxes=("filing@singularity.tax",),
       hints=None, headers=None):
    return Event(
        uid=uid, source="mail", timestamp=ts, direction=direction, sender=sender,
        recipients=list(recipients), subject=subject, body=body, mailboxes=list(mailboxes),
        thread_hints=list(hints or [f"mid:{uid}"]), headers=dict(headers or {}),
    )
