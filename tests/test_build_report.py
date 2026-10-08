from datetime import datetime, timedelta, timezone

import yaml

from slamonitor.cli import main
from slamonitor.store import Store

from .conftest import mk


def test_build_writes_report(tmp_path):
    data_dir = tmp_path / "data"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "tenant_id": "t", "client_id": "c", "data_dir": str(data_dir),
        "internal_domains": ["singularity.tax"],
        "shared_mailboxes": ["filing@singularity.tax"],
        "staff": [{"email": "anna.test@singularity.tax", "name": "Anna Test", "signature_names": ["Anna T"]}],
        "auto_ack_patterns": ["Reference: SNG-"],
        "bitrix": {"enabled": False},
    }))
    now = datetime.now(timezone.utc)
    data_dir.mkdir()
    store = Store(data_dir / "monitor.sqlite")
    store.upsert_event(mk("<r1@shop>", now - timedelta(days=3), "in", "c@shop.example", body="Where is <b>my</b> filing?"))
    store.upsert_event(mk("<r2@shop>", now - timedelta(days=2), "in", "d@shop.example", subject="Other", body="Question?"))
    store.upsert_event(mk("<a2@sing>", now - timedelta(days=1), "out", "anna.test@singularity.tax", ["d@shop.example"],
                          subject="RE: Other", body="Answer attached.", mailboxes=["anna.test@singularity.tax"],
                          hints=["mid:<a2@sing>", "mid:<r2@shop>"]))
    store.close()

    assert main(["--config", str(cfg_path), "build", "--days", "30"]) == 0
    reports = list((data_dir / "reports").glob("baseline-*.html"))
    assert len(reports) == 1
    page = reports[0].read_text()
    assert "Antwortzeiten – Baseline" in page
    assert "Anna Test" in page
    assert list((data_dir / "reports").glob("faelle-*.csv"))
