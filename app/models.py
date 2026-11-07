from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy.dialects.postgresql import JSONB
from werkzeug.security import check_password_hash, generate_password_hash

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    paypal_transaction_id = db.Column(
        db.String(64), unique=True, nullable=False, index=True)
    customer_name = db.Column(db.String(120))
    customer_email = db.Column(db.String(255))
    item_description = db.Column(db.String(255))
    amount = db.Column(db.Numeric(12, 2))
    currency = db.Column(db.String(3))
    ordered_at = db.Column(db.DateTime(timezone=True))

    shipments = db.relationship(
        "Shipment", backref="order", order_by="Shipment.id")
    messages = db.relationship(
        "Message", backref="order", order_by="Message.sent_at")


class Shipment(db.Model):
    __tablename__ = "shipments"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey(
        "orders.id"), nullable=False, index=True)
    carrier = db.Column(db.String(32))
    tracking_number = db.Column(db.String(64), index=True)
    shipped_at = db.Column(db.DateTime(timezone=True))
    # empty = not delivered yet
    delivered_at = db.Column(db.DateTime(timezone=True))


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey(
        "orders.id"), nullable=False, index=True)
    sender = db.Column(db.String(16))  # "seller" or "buyer"
    body = db.Column(db.Text)
    sent_at = db.Column(db.DateTime(timezone=True))


class Dispute(db.Model):
    __tablename__ = "disputes"

    id = db.Column(db.Integer, primary_key=True)
    paypal_dispute_id = db.Column(
        db.String(64), unique=True, nullable=False, index=True)
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
    updated_at = db.Column(db.DateTime(timezone=True),
                           default=utcnow, onupdate=utcnow)

    order = db.relationship("Order")


class WebhookEvent(db.Model):
    __tablename__ = "webhook_events"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(64), unique=True, nullable=False)
    event_type = db.Column(db.String(64))
    resource_id = db.Column(db.String(64))
    received_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class AgentRun(db.Model):
    __tablename__ = "agent_runs"

    id = db.Column(db.Integer, primary_key=True)
    dispute_id = db.Column(db.Integer, db.ForeignKey(
        "disputes.id"), nullable=False, index=True)
    model = db.Column(db.String(64))
    input_context = db.Column(JSONB)
    raw_output = db.Column(db.Text)
    proposal = db.Column(JSONB)
    status = db.Column(db.String(16))
    error = db.Column(db.Text)
    policy = db.Column(JSONB)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    dispute = db.relationship("Dispute", backref="agent_runs")


class Action(db.Model):
    __tablename__ = "actions"

    id = db.Column(db.Integer, primary_key=True)
    dispute_id = db.Column(db.Integer, db.ForeignKey("disputes.id"), nullable=False, index=True)
    agent_run_id = db.Column(db.Integer, db.ForeignKey("agent_runs.id"))
    action_type = db.Column(db.String(32))
    amount = db.Column(db.Numeric(12, 2))
    idempotency_key = db.Column(db.String(64), unique=True, nullable=False)
    status = db.Column(db.String(16))  # pending, sent, failed
    approved_by = db.Column(db.String(64))
    paypal_response = db.Column(JSONB)
    error = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    completed_at = db.Column(db.DateTime(timezone=True))

    dispute = db.relationship("Dispute", backref="actions")


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    dispute_id = db.Column(db.Integer, db.ForeignKey("disputes.id"), index=True)
    actor = db.Column(db.String(16))  # system, ai, seller, paypal
    event = db.Column(db.String(48))
    detail = db.Column(JSONB)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    dispute = db.relationship("Dispute", backref="audit_entries")


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Setting(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    refund_threshold = db.Column(db.Numeric(12, 2), nullable=False, default=15)
    urgent_hours = db.Column(db.Integer, nullable=False, default=48)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
