from datetime import datetime, timezone

from app.labels import label
from app.models import Action, AgentRun, Dispute
from app.services.policy import is_urgent
from app.services.settings import get_settings

WAITING_ON_SELLER = {"OPEN", "WAITING_FOR_SELLER_RESPONSE"}


def _recommendation(run, sent):
    if sent:
        return "Sent to PayPal"
    if run is None:
        return None
    if run.status != "ok":
        return "Needs review"
    return run.proposal["recommendation"].replace("_", " ").capitalize()


def dashboard_rows(now=None):
    now = now or datetime.now(timezone.utc)
    hours = get_settings().urgent_hours
    latest_run = {run.dispute_id: run for run in AgentRun.query.order_by(AgentRun.id)}
    sent = {a.dispute_id for a in Action.query.filter_by(status="sent")}
    disputes = Dispute.query.order_by(Dispute.seller_response_due.asc().nulls_last(), Dispute.id).all()

    rows = [
        {
            "id": d.paypal_dispute_id,
            "reason": label(d.reason),
            "status": label(d.status),
            "status_code": d.status,
            "stage": label(d.stage),
            "amount": float(d.amount) if d.amount is not None else None,
            "currency": d.currency or "USD",
            "due": d.seller_response_due.isoformat() if d.seller_response_due else None,
            "urgent": is_urgent(d, now=now, hours=hours),
            "waiting_on_you": d.status in WAITING_ON_SELLER,
            "recommendation": _recommendation(latest_run.get(d.id), d.id in sent),
        }
        for d in disputes
    ]

    active = [d for d in disputes if d.status != "RESOLVED"]
    summary = {
        "open": len(active),
        "waiting_on_you": sum(d.status in WAITING_ON_SELLER for d in active),
        "urgent": sum(r["urgent"] for r in rows),
        "at_stake": f"{sum(float(d.amount or 0) for d in active):.2f}",
    }
    return rows, summary
