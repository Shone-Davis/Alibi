from datetime import datetime

from app import db
from app.models import Dispute, Order


def _parse_time(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _allowed_actions(dispute):
    return [l["rel"] for l in dispute.get("links", []) if l.get("rel") != "self"]


def save_dispute(data):
    """Create or update one dispute row from PayPal's full dispute JSON."""
    pid = data["dispute_id"]
    row = Dispute.query.filter_by(paypal_dispute_id=pid).first()
    if row is None:
        row = Dispute(paypal_dispute_id=pid)
        db.session.add(row)

    row.reason = data.get("reason")
    row.status = data.get("status")
    row.stage = data.get("dispute_life_cycle_stage")
    amount = data.get("dispute_amount") or {}
    row.amount = amount.get("value")
    row.currency = amount.get("currency_code")
    txns = data.get("disputed_transactions") or [{}]
    row.seller_transaction_id = txns[0].get("seller_transaction_id")
    row.allowed_actions = _allowed_actions(data)
    row.raw = data

    # PayPal drops the deadline later: only overwrite when a new one is present
    due = _parse_time(data.get("seller_response_due_date"))
    if due is not None:
        row.seller_response_due = due

    # match the dispute to the shop's order by PayPal transaction ID
    if row.seller_transaction_id:
        order = Order.query.filter_by(paypal_transaction_id=row.seller_transaction_id).first()
        if order:
            row.order_id = order.id

    return row


def sync_dispute(client, dispute_id):
    row = save_dispute(client.get_dispute(dispute_id))
    db.session.commit()
    return row


def sync_all(client):
    items = client.list_disputes(page_size=50)
    for item in items:
        save_dispute(client.get_dispute(item["dispute_id"]))
    db.session.commit()
    return len(items)
