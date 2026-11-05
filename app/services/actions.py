"""Sends a seller-approved proposal to PayPal, exactly once."""
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Action
from app.paypal.client import PayPalError
from app.services.audit import record
from app.services.policy import check_execution
from app.services.sync import save_dispute


class ActionBlocked(Exception):
    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _evidences(dispute, proposal):
    # Tracking details come from our shipment rows, never from the AI's text.
    cited = {e["record_id"] for e in proposal.get("evidence") or []}
    shipments = [
        s for s in (dispute.order.shipments if dispute.order else [])
        if f"shipment:{s.id}" in cited and s.tracking_number
    ]
    if shipments:
        return [{
            "evidence_type": "PROOF_OF_FULFILLMENT",
            "evidence_info": {"tracking_info": [
                {"carrier_name": s.carrier, "tracking_number": s.tracking_number} for s in shipments
            ]},
            "notes": proposal["draft_note"],
        }]
    return [{"evidence_type": "OTHER", "notes": proposal["draft_note"]}]


def _send(client, dispute, proposal, action_type, key):
    pid = dispute.paypal_dispute_id
    note = proposal["draft_note"]
    if action_type == "provide_evidence":
        return client.provide_evidence(pid, _evidences(dispute, proposal), request_id=key)
    if action_type == "provide_supporting_info":
        return client.provide_supporting_info(pid, note, request_id=key)
    if action_type == "make_offer":
        return client.make_offer(pid, note, proposal["offer_amount"], dispute.currency or "USD", request_id=key)
    if action_type == "accept_claim":
        return client.accept_claim(pid, note, request_id=key)
    raise ValueError(f"Unsupported action {action_type}")


def approve(run, approved_by, client):
    key = f"agent-run-{run.id}"
    existing = Action.query.filter_by(idempotency_key=key).first()
    if existing:
        return existing

    if not run.proposal:
        raise ActionBlocked(["This run has no proposal to approve"])

    dispute = run.dispute
    # Allowed actions go stale, so confirm PayPal's current state before sending.
    try:
        save_dispute(client.get_dispute(dispute.paypal_dispute_id))
    except PayPalError as e:
        raise ActionBlocked([f"Couldn't confirm the dispute's current state with PayPal: {e}"])

    result = check_execution(dispute, run.proposal, approved_by)
    if not result.allowed:
        record(dispute, "system", "action_blocked", {"agent_run_id": run.id, "reasons": result.reasons})
        db.session.commit()
        raise ActionBlocked(result.reasons)

    amount = None
    if result.action == "make_offer":
        amount = run.proposal["offer_amount"]
    elif result.action == "accept_claim":
        amount = dispute.amount

    action = Action(
        dispute_id=dispute.id,
        agent_run_id=run.id,
        action_type=result.action,
        amount=amount,
        idempotency_key=key,
        status="pending",
        approved_by=approved_by,
    )
    db.session.add(action)
    record(dispute, "seller", "seller_approved",
           {"agent_run_id": run.id, "action": result.action, "approved_by": approved_by})
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return Action.query.filter_by(idempotency_key=key).one()

    try:
        response = _send(client, dispute, run.proposal, result.action, key)
    except PayPalError as e:
        action.status = "failed"
        action.error = str(e)
        record(dispute, "paypal", "paypal_action_failed",
               {"action": result.action, "status": e.status, "debug_id": e.debug_id})
        db.session.commit()
        return action

    action.status = "sent"
    action.paypal_response = response
    action.completed_at = datetime.now(timezone.utc)
    record(dispute, "paypal", "paypal_action_sent", {"action": result.action})
    try:
        save_dispute(client.get_dispute(dispute.paypal_dispute_id))
    except PayPalError:
        pass  # the next webhook brings the new status
    db.session.commit()
    return action
