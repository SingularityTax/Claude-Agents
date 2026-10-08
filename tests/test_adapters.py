import json

from slamonitor.audit import parse_audit_csv
from slamonitor.graph import to_event
from slamonitor.store import Store

from .conftest import at, mk


def test_graph_to_event_inbound_and_headers(cfg):
    msg = {
        "internetMessageId": "<abc@gmail.com>",
        "conversationId": "CONV1",
        "subject": "Re: URGENT",
        "from": {"emailAddress": {"address": "Client@Gmail.com", "name": "Client"}},
        "toRecipients": [{"emailAddress": {"address": "filing@singularity.tax"}}],
        "ccRecipients": [],
        "receivedDateTime": "2026-10-08T13:18:07Z",
        "sentDateTime": "2026-10-08T13:17:49Z",
        "uniqueBody": {"content": "Can you please update?"},
        "internetMessageHeaders": [{"name": "In-Reply-To", "value": "<prev@sing>"},
                                   {"name": "References", "value": "<root@gmail.com> <prev@sing>"}],
        "webLink": "https://outlook/x",
    }
    ev = to_event(cfg, "filing@singularity.tax", msg)
    assert ev.direction == "in" and ev.sender == "client@gmail.com"
    assert {"mid:<prev@sing>", "mid:<root@gmail.com>", "conv:filing@singularity.tax:CONV1"} <= set(ev.thread_hints)


def test_graph_to_event_outbound_uses_sent_time(cfg):
    msg = {
        "internetMessageId": "<x@sing>",
        "from": {"emailAddress": {"address": "filing@singularity.tax"}},
        "toRecipients": [{"emailAddress": {"address": "c@shop.example"}}],
        "receivedDateTime": "2026-10-08T16:44:18Z",
        "sentDateTime": "2026-10-08T16:44:17Z",
    }
    ev = to_event(cfg, "filing@singularity.tax", msg)
    assert ev.direction == "out"
    assert ev.timestamp.second == 17


def test_graph_internal_only_and_drafts(cfg):
    internal = {"internetMessageId": "<i@sing>", "from": {"emailAddress": {"address": "a@singularity.tax"}},
                "toRecipients": [{"emailAddress": {"address": "b@singularity.tax"}}],
                "receivedDateTime": "2026-10-08T10:00:00Z", "sentDateTime": "2026-10-08T10:00:00Z"}
    assert to_event(cfg, "filing@singularity.tax", internal).direction == "internal"
    assert to_event(cfg, "filing@singularity.tax", {**internal, "isDraft": True}) is None


def test_store_merges_mailboxes(cfg, tmp_path):
    store = Store(tmp_path / "t.sqlite")
    store.upsert_event(mk("<r@x>", at(5, 9), "in", "c@x.example", mailboxes=["hello@singularity.tax"]))
    store.upsert_event(mk("<r@x>", at(5, 9), "in", "c@x.example", mailboxes=["welcome@singularity.tax"],
                          hints=["mid:<r@x>", "conv:welcome:1"]))
    [ev] = store.events()
    assert ev.mailboxes == ["hello@singularity.tax", "welcome@singularity.tax"]
    assert "conv:welcome:1" in ev.thread_hints
    store.close()


def test_audit_csv(tmp_path):
    p = tmp_path / "a.csv"
    data = {"Operation": "SendAs", "UserId": "Monica.Jayappa@singularity.tax",
            "MailboxOwnerUPN": "filing@singularity.tax", "CreationTime": "2026-10-08T16:36:30",
            "Item": {"InternetMessageId": "<FR4P@x>", "Subject": "RE: VAT"}}
    other = {"Operation": "MailItemsAccessed", "UserId": "x"}
    with p.open("w", encoding="utf-8") as f:
        f.write("CreationDate,UserIds,Operations,AuditData\n")
        for d in (data, other):
            f.write('"2026-10-08","u","op","' + json.dumps(d).replace('"', '""') + '"\n')
    rows = parse_audit_csv(p)
    assert rows == [("<FR4P@x>", "monica.jayappa@singularity.tax", "filing@singularity.tax", "2026-10-08T16:36:30")]
