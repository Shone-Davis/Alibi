import json

import pytest

from app.models import Dispute, WebhookEvent

SIG_HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert",
    "PAYPAL-TRANSMISSION-ID": "t-1",
    "PAYPAL-TRANSMISSION-SIG": "sig",
    "PAYPAL-TRANSMISSION-TIME": "2026-11-01T00:00:00Z",
}


class FakePayPal:
    def __init__(self):
        self.signature_valid = True
        self.fetched = []

    def verify_webhook_signature(self, sig, raw_body, webhook_id):
        return self.signature_valid

    def get_dispute(self, dispute_id):
        self.fetched.append(dispute_id)
        return {
            "dispute_id": dispute_id,
            "status": "WAITING_FOR_SELLER_RESPONSE",
            "dispute_life_cycle_stage": "INQUIRY",
            "dispute_amount": {"currency_code": "USD", "value": "12.00"},
            "seller_response_due_date": "2026-11-12T12:00:00.000Z",
            "links": [{"rel": "provide_evidence", "method": "POST", "href": "x"}],
        }


@pytest.fixture
def fake(app, monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-TEST")
    fake = FakePayPal()
    app.extensions["paypal"] = fake
    return fake


def event(event_id="WH-1", event_type="CUSTOMER.DISPUTE.CREATED", dispute_id="PP-D-1"):
    return json.dumps(
        {"id": event_id, "event_type": event_type,
            "resource": {"dispute_id": dispute_id}}
    )


def post(app, body, headers=SIG_HEADERS):
    return app.test_client().post(
        "/webhooks/paypal", data=body, headers=headers, content_type="application/json"
    )


# T5: valid signature - 200, dispute synced

def test_t5_valid_event_syncs_dispute(app, fake):
    r = post(app, event())

    assert r.status_code == 200
    assert fake.fetched == ["PP-D-1"]
    row = Dispute.query.filter_by(paypal_dispute_id="PP-D-1").one()
    assert row.seller_response_due is not None
    assert WebhookEvent.query.count() == 1


# T6: missing or fake signature -> 400, nothing saved

def test_t6_missing_signature_headers_rejected(app, fake):
    r = post(app, event(), headers={})

    assert r.status_code == 400
    assert fake.fetched == []
    assert Dispute.query.count() == 0
    assert WebhookEvent.query.count() == 0


def test_t6_fake_signature_rejected(app, fake):
    fake.signature_valid = False
    r = post(app, event())

    assert r.status_code == 400
    assert fake.fetched == []
    assert Dispute.query.count() == 0
    assert WebhookEvent.query.count() == 0


# T7: same event twice - processed once

def test_t7_duplicate_event_processed_once(app, fake):
    first = post(app, event(event_id="WH-DUP"))
    second = post(app, event(event_id="WH-DUP"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert fake.fetched == ["PP-D-1"]  # PayPal only asked once
    assert WebhookEvent.query.count() == 1


# T8: unknown event type ignored with 200; GET - 405

def test_t8_unknown_event_type_ignored(app, fake):
    r = post(app, event(event_type="PAYMENT.CAPTURE.COMPLETED"))

    assert r.status_code == 200
    assert fake.fetched == []
    assert Dispute.query.count() == 0


def test_t8_get_request_not_allowed(app, fake):
    r = app.test_client().get("/webhooks/paypal")

    assert r.status_code == 405
