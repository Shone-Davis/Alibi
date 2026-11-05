import json
import logging
import os

from flask import Blueprint, current_app, request
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import WebhookEvent
from app.paypal.client import PayPalError
from app.services.audit import record
from app.services.sync import save_dispute

log = logging.getLogger(__name__)
bp = Blueprint("webhooks", __name__)

SIG_HEADERS = {
    "auth_algo": "PAYPAL-AUTH-ALGO",
    "cert_url": "PAYPAL-CERT-URL",
    "transmission_id": "PAYPAL-TRANSMISSION-ID",
    "transmission_sig": "PAYPAL-TRANSMISSION-SIG",
    "transmission_time": "PAYPAL-TRANSMISSION-TIME",
}


@bp.route("/webhooks/paypal", methods=["POST"])
def paypal_webhook():
    raw = request.get_data(as_text=True)
    client = current_app.extensions["paypal"]

    # Is it really from PayPal
    sig = {key: request.headers.get(header)
           for key, header in SIG_HEADERS.items()}
    if not all(sig.values()):
        return {"error": "missing signature headers"}, 400
    webhook_id = os.environ.get("PAYPAL_WEBHOOK_ID")
    if not webhook_id:
        log.error("PAYPAL_WEBHOOK_ID is not set")
        return {"error": "server not configured"}, 500
    try:
        verified = client.verify_webhook_signature(sig, raw, webhook_id)
    except PayPalError:
        verified = False
    if not verified:
        log.warning("Rejected webhook with bad signature")
        return {"error": "invalid signature"}, 400

    try:
        event = json.loads(raw)
    except ValueError:
        return {"error": "invalid JSON"}, 400
    event_id = event.get("id")
    if not event_id:
        return {"error": "missing event id"}, 400

    # Seen it before! Do nothing.
    if WebhookEvent.query.filter_by(event_id=event_id).first():
        return {"status": "duplicate"}, 200

    # Refresh the dispute and record the event together
    event_type = event.get("event_type", "")
    resource_id = (event.get("resource") or {}).get("dispute_id")
    try:
        if event_type.startswith("CUSTOMER.DISPUTE.") and resource_id:
            row = save_dispute(client.get_dispute(resource_id))
            record(row, "paypal", "webhook_received", {"event_id": event_id, "event_type": event_type})
        db.session.add(
            WebhookEvent(event_id=event_id, event_type=event_type,
                         resource_id=resource_id)
        )
        db.session.commit()
    except IntegrityError:  # same event arrived twice at the same moment
        db.session.rollback()
        return {"status": "duplicate"}, 200
    except Exception:
        db.session.rollback()
        log.exception("Webhook processing failed for %s", event_id)
        return {"error": "processing failed"}, 500

    return {"status": "ok"}, 200
