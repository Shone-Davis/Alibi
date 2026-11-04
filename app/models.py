from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import JSONB

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    paypal_transaction_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    customer_name = db.Column(db.String(120))
    customer_email = db.Column(db.String(255))
    item_description = db.Column(db.String(255))
    amount = db.Column(db.Numeric(12, 2))
    currency = db.Column(db.String(3))
    ordered_at = db.Column(db.DateTime(timezone=True))

    shipments = db.relationship("Shipment", backref="order", order_by="Shipment.id")
    messages = db.relationship("Message", backref="order", order_by="Message.sent_at")


class Shipment(db.Model):
    __tablename__ = "shipments"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    carrier = db.Column(db.String(32))
    tracking_number = db.Column(db.String(64), index=True)
    shipped_at = db.Column(db.DateTime(timezone=True))
    delivered_at = db.Column(db.DateTime(timezone=True))  # empty = not delivered yet


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    sender = db.Column(db.String(16))  # "seller" or "buyer"
    body = db.Column(db.Text)
    sent_at = db.Column(db.DateTime(timezone=True))


class Dispute(db.Model):
    __tablename__ = "disputes"

    id = db.Column(db.Integer, primary_key=True)
    paypal_dispute_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    reason = db.Column(db.String(64))
    status = db.Column(db.String(64))
    stage = db.Column(db.String(32))  # INQUIRY, CHARGEBACK, ...
    amount = db.Column(db.Numeric(12, 2))
    currency = db.Column(db.String(3))
    seller_transaction_id = db.Column(db.String(64), index=True)
    seller_response_due = db.Column(db.DateTime(timezone=True))
    allowed_actions = db.Column(JSONB, default=list)
    raw = db.Column(JSONB)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), index=True)
    first_seen_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    order = db.relationship("Order")


class WebhookEvent(db.Model):
    __tablename__ = "webhook_events"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(64), unique=True, nullable=False)
    event_type = db.Column(db.String(64))
    resource_id = db.Column(db.String(64))
    received_at = db.Column(db.DateTime(timezone=True), default=utcnow)
