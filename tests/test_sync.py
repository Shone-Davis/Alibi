from datetime import datetime, timezone

from app import db
from app.models import Dispute
from app.services.sync import save_dispute, sync_dispute


def paypal_dispute(**overrides):
    data = {
        "dispute_id": "PP-D-100",
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "status": "WAITING_FOR_SELLER_RESPONSE",
        "dispute_life_cycle_stage": "INQUIRY",
        "dispute_amount": {"currency_code": "USD", "value": "25.00"},
        "disputed_transactions": [{"seller_transaction_id": "TXN123"}],
        "seller_response_due_date": "2026-11-10T12:00:00.000Z",
        "links": [
            {"rel": "self", "method": "GET", "href": "https://x"},
            {"rel": "provide_evidence", "method": "POST", "href": "https://y"},
        ],
    }
    data.update(overrides)
    return data


class FakeClient:
    def __init__(self, data):
        self.data = data

    def get_dispute(self, dispute_id):
        return self.data


DEADLINE = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


# T9: new dispute arrives

def test_t9_new_dispute_creates_row_with_deadline_and_actions(app):
    sync_dispute(FakeClient(paypal_dispute()), "PP-D-100")

    row = Dispute.query.filter_by(paypal_dispute_id="PP-D-100").one()
    assert row.stage == "INQUIRY"
    assert str(row.amount) == "25.00"
    assert row.seller_response_due == DEADLINE
    assert row.allowed_actions == ["provide_evidence"]
    assert row.seller_transaction_id == "TXN123"  # used for order matching


def test_t9_syncing_twice_updates_instead_of_duplicating(app):
    client = FakeClient(paypal_dispute())
    sync_dispute(client, "PP-D-100")
    sync_dispute(client, "PP-D-100")

    assert Dispute.query.count() == 1


# T10: later update has no deadline

def test_t10_missing_deadline_later_keeps_original(app):
    save_dispute(paypal_dispute())
    db.session.commit()

    later = paypal_dispute(status="UNDER_REVIEW",
                           dispute_life_cycle_stage="CHARGEBACK")
    del later["seller_response_due_date"]
    save_dispute(later)
    db.session.commit()
    db.session.expire_all()

    row = Dispute.query.one()
    assert row.status == "UNDER_REVIEW"
    assert row.stage == "CHARGEBACK"
    assert row.seller_response_due == DEADLINE


def test_t9_dispute_matched_to_order_by_transaction_id(app):
    from app.models import Order

    db.session.add(Order(paypal_transaction_id="TXN123", amount=25))
    db.session.commit()

    sync_dispute(FakeClient(paypal_dispute()), "PP-D-100")

    row = Dispute.query.one()
    assert row.order is not None
    assert row.order.paypal_transaction_id == "TXN123"
