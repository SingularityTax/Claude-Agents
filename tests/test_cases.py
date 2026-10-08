from slamonitor.cases import (add_derived_hints, attribute_responder, build_cases, build_threads,
                              normalize_subject)
from slamonitor.classify import classify_event
from slamonitor.hours import BusinessClock

from .conftest import at, mk


def run(cfg, events, now, audit=None, owners=None):
    for ev in events:
        add_derived_hints(cfg, ev)
        classify_event(cfg, ev)
        attribute_responder(cfg, ev, audit or {})
    return build_cases(cfg, BusinessClock(cfg.sla), build_threads(events), now, owners)


def test_normalize_subject():
    assert normalize_subject("RE: AW: FW: VAT Annual Return 2025") == "vat annual return 2025"
    assert normalize_subject("Urgent Reference: SNG-20260903-694601") == ""


def test_answered_in_sla_by_personal_mailbox(cfg):
    req = mk("<r1@shop>", at(5, 9), "in", "c@shop.example", body="Where is my July filing?")
    rep = mk("<p1@sing>", at(5, 15), "out", "anna.test@singularity.tax", ["c@shop.example"],
             subject="RE: VAT return", body="Filed on 10 August, proof attached.",
             mailboxes=["anna.test@singularity.tax"], hints=["mid:<p1@sing>", "mid:<r1@shop>"])
    [case] = run(cfg, [req, rep], at(8, 12))
    assert case.status == "answered_in_sla"
    assert case.responder == "anna.test@singularity.tax"
    assert case.responder_via == "personal"
    assert case.response_business_hours == 6.0


def test_fake_closure_keeps_case_open(cfg):
    req = mk("<r1@shop>", at(5, 9), "in", "c@shop.example", subject="DE Garnishment order",
             body="Our account is blocked, please help.", mailboxes=["welcome@singularity.tax"])
    ack = mk("<a1@sing>", at(5, 9, 1), "out", "welcome@singularity.tax", ["c@shop.example"],
             subject="Re: DE Garnishment order",
             body="We confirm receipt of your enquiry (Reference: SNG-20261005-EDD34A).",
             mailboxes=["welcome@singularity.tax"], hints=["mid:<a1@sing>", "mid:<r1@shop>"])
    closure = mk("<a2@sing>", at(5, 9, 3), "out", "welcome@singularity.tax", ["c@shop.example"],
                 subject="Re: DE Garnishment order",
                 body="Your enquiry (Reference: SNG-20261005-EDD34A) has been reviewed and addressed by our team.",
                 mailboxes=["welcome@singularity.tax"], hints=["mid:<a2@sing>", "mid:<r1@shop>"])
    [case] = run(cfg, [req, ack, closure], at(8, 12))
    assert case.priority == "P1"
    assert case.status == "open_overdue"
    assert case.auto_closures == 1 and case.auto_acks == 1
    assert case.response_at is None


def test_shared_mailbox_reply_attributed_via_audit_then_signature(cfg):
    req = mk("<r1@shop>", at(5, 9), "in", "c@shop.example", body="Need the draft in English")
    rep = mk("<s1@sing>", at(6, 9), "out", "filing@singularity.tax", ["c@shop.example"],
             subject="RE: VAT return", body="Please find attached the draft.",
             hints=["mid:<s1@sing>", "mid:<r1@shop>"])
    [case] = run(cfg, [req, rep], at(8, 12), audit={"<s1@sing>": "Ben.Test@singularity.tax"})
    assert case.responder == "ben.test@singularity.tax" and case.responder_via == "audit"
    assert case.status == "answered_in_sla"  # Mo 09:00 -> Di 09:00 = 10 h

    req2 = mk("<r2@shop>", at(5, 9), "in", "d@shop.example", subject="Proof", body="Proof of submission?")
    rep2 = mk("<s2@sing>", at(7, 9), "out", "filing@singularity.tax", ["d@shop.example"], subject="RE: Proof",
              body="Please find the proof of submission.\n\nRegards ,\nAnna T\n\nFrom: d@shop.example",
              hints=["mid:<s2@sing>", "mid:<r2@shop>"])
    [case2] = run(cfg, [req2, rep2], at(8, 12))
    assert case2.responder == "anna.test@singularity.tax" and case2.responder_via == "signature"
    assert case2.status == "answered_late"


def test_followups_and_subject_threading_without_headers(cfg):
    # Mandant schreibt zweimal ohne Antwort; zweite Mail hat keinen In-Reply-To, nur gleichen Betreff
    r1 = mk("<r1@shop>", at(1, 13), "in", "c@shop.example", subject="Umsatzsteuer-Sonderprüfung 2023",
            body="Can you update me?")
    r2 = mk("<r2@shop>", at(8, 15), "in", "c@shop.example", subject="Re: Umsatzsteuer-Sonderprüfung 2023",
            body="Can you please update on this issue?")
    [case] = run(cfg, [r1, r2], at(8, 16))
    assert case.followups == 1
    assert case.status == "open_overdue"


def test_dedup_thread_across_mailboxes_and_owner(cfg):
    req = mk("<r1@shop>", at(5, 10), "in", "c@shop.example", body="Status of our registration?",
             mailboxes=["hello@singularity.tax", "welcome@singularity.tax"])
    [case] = run(cfg, [req], at(5, 12), owners={"c@shop.example": "anna.test@singularity.tax"})
    assert case.status == "open_in_sla"
    assert case.owner == "anna.test@singularity.tax"
    assert set(case.mailboxes) == {"hello@singularity.tax", "welcome@singularity.tax"}


def test_noise_creates_no_case(cfg):
    ev = mk("<n1@x>", at(5, 10), "in", "noreply@bitrix24.com", subject="You've been invited to Bitrix24")
    assert run(cfg, [ev], at(8, 12)) == []


def test_new_request_after_answer_opens_second_case(cfg):
    r1 = mk("<r1@shop>", at(5, 9), "in", "c@shop.example", body="Question one?")
    a1 = mk("<a1@sing>", at(5, 10), "out", "anna.test@singularity.tax", ["c@shop.example"], subject="RE: VAT return",
            body="Answer one, see attached.", hints=["mid:<a1@sing>", "mid:<r1@shop>"])
    r2 = mk("<r2@shop>", at(6, 9), "in", "c@shop.example", subject="RE: VAT return", body="Follow-up question?",
            hints=["mid:<r2@shop>", "mid:<a1@sing>"])
    cases = run(cfg, [r1, a1, r2], at(6, 10))
    assert [c.status for c in cases] == ["answered_in_sla", "open_in_sla"]
