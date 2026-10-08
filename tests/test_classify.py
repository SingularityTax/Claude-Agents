from slamonitor.classify import classify_event

from .conftest import at, mk


def kind(cfg, ev):
    classify_event(cfg, ev)
    return ev.kind


def test_client_request(cfg):
    ev = mk("a", at(5, 9), "in", "client@shop.example", body="Can you send the proof of filing for July?")
    assert kind(cfg, ev) == "client_request"
    assert ev.priority == "P2"


def test_urgent_garnishment(cfg):
    ev = mk("a", at(5, 9), "in", "client@shop.example", subject="DE Garnishment order received",
            body="Our Amazon account has been blocked.")
    classify_event(cfg, ev)
    assert ev.priority == "P1"


def test_finanzamt_alone_is_not_urgent(cfg):
    ev = mk("a", at(5, 9), "in", "client@shop.example", body="Das Finanzamt hat einen Bescheid geschickt, anbei.")
    classify_event(cfg, ev)
    assert ev.priority == "P2"


def test_noise(cfg):
    assert kind(cfg, mk("a", at(5, 9), "in", "noreply@nubiance.fr")) == "noise"
    assert kind(cfg, mk("b", at(5, 9), "in", "recruiter@agency.example")) == "noise"
    assert kind(cfg, mk("c", at(5, 9), "in", "x@shop.example", headers={"List-Unsubscribe": "<mailto:u>"})) == "noise"
    assert kind(cfg, mk("d", at(5, 9), "in", "x@shop.example", subject="Automatic reply: VAT")) == "noise"


def test_thanks_without_question(cfg):
    assert kind(cfg, mk("a", at(5, 9), "in", "c@shop.example", body="Thanks a lot for your response.")) == "thanks"
    assert kind(cfg, mk("b", at(5, 9), "in", "c@shop.example", body="Thanks. When is the next filing?")) == "client_request"


def test_auto_ack_and_closure(cfg):
    ack = mk("a", at(5, 9), "out", "filing@singularity.tax", ["c@shop.example"],
             body="Thank you for your message. We confirm receipt of your enquiry (Reference: SNG-20261008-C97BD3).")
    closure = mk("b", at(5, 9), "out", "filing@singularity.tax", ["c@shop.example"],
                 body="We can confirm that your enquiry (Reference: SNG-20261008-C97BD3) has been reviewed and addressed by our team.")
    assert kind(cfg, ack) == "auto_ack"
    assert kind(cfg, closure) == "auto_closure"


def test_reply_vs_interim(cfg):
    assert kind(cfg, mk("a", at(5, 9), "out", "anna.test@singularity.tax", ["c@shop.example"],
                        body="Please find attached the proof of submission.\n\nRegards, Anna T")) == "reply"
    assert kind(cfg, mk("b", at(5, 9), "out", "anna.test@singularity.tax", ["c@shop.example"],
                        body="We are looking into it and will get back to you.")) == "interim"
