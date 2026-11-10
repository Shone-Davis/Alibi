import pytest

from app import db
from app.models import Action, AgentRun, AuditLog, Dispute, Order, Shipment
from app.paypal.client import PayPalError
from app.services.actions import ActionBlocked, approve
from app.services.sync import save_dispute

TRACKING = "9400111222333444555666"


def paypal_json(stage="INQUIRY", status="WAITING_FOR_SELLER_RESPONSE", actions=("provide_evidence",)):
    return {
        "dispute_id": "PP-D-1",
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "status": status,
        "dispute_life_cycle_stage": stage,
        "dispute_amount": {"currency_code": "USD", "value": "25.00"},
        "disputed_transactions": [{"seller_transaction_id": "TXN1"}],
        "links": [{"rel": a, "method": "POST", "href": "x"} for a in actions],
    }


class FakePayPal:
    def __init__(self, before, after=None):
        self.state = before
        self.after = after
        self.sent = []

    def get_dispute(self, dispute_id):
        return self.state

    def _record(self, name, payload, request_id):
        self.sent.append((name, payload, request_id))
        if self.after:
            self.state = self.after
        return {"links": []}

    def provide_evidence(self, dispute_id, evidences, request_id=None):
        return self._record("provide_evidence", evidences, request_id)

    def provide_supporting_info(self, dispute_id, notes, request_id=None):
        return self._record("provide_supporting_info", notes, request_id)

    def make_offer(self, dispute_id, note, amount, currency, request_id=None):
        return self._record("make_offer", amount, request_id)

    def accept_claim(self, dispute_id, note, request_id=None):
        return self._record("accept_claim", note, request_id)


class RejectingPayPal(FakePayPal):
    def provide_evidence(self, dispute_id, evidences, request_id=None):
        raise PayPalError(422, "Action not allowed", "dbg-422")


def setup_case(paypal):
    order = Order(paypal_transaction_id="TXN1", amount=25, currency="USD")
    db.session.add(order)
    db.session.flush()
    ship = Shipment(order_id=order.id, carrier="USPS", tracking_number=TRACKING)
    db.session.add(ship)
    db.session.commit()

    dispute = save_dispute(paypal.get_dispute("PP-D-1"))
    db.session.commit()

    run = AgentRun(dispute_id=dispute.id, status="ok", proposal={
        "recommendation": "fight",
        "confidence": 0.9,
        "reasoning": "Delivered.",
        "evidence": [{"record_id": f"shipment:{ship.id}", "claim": "Delivered"}],
        "offer_amount": None,
        "draft_note": f"The seller shipped via USPS, tracking {TRACKING}.",
    })
    db.session.add(run)
    db.session.commit()
    return dispute, run


def test_t21_fight_at_inquiry_sends_evidence_and_saves_new_status(app):
    paypal = FakePayPal(paypal_json(), paypal_json(status="WAITING_FOR_BUYER_RESPONSE", actions=()))
    _, run = setup_case(paypal)

    action = approve(run, approved_by="seller", client=paypal)

    assert action.status == "sent"
    name, evidences, request_id = paypal.sent[0]
    assert name == "provide_evidence"
    assert evidences[0]["evidence_info"]["tracking_info"][0]["tracking_number"] == TRACKING
    assert request_id == action.idempotency_key
    assert Dispute.query.one().status == "WAITING_FOR_BUYER_RESPONSE"
    events = [a.event for a in AuditLog.query.order_by(AuditLog.id)]
    assert events == ["seller_approved", "paypal_action_sent"]


def test_t22_fight_at_chargeback_sends_supporting_info(app):
    chargeback = paypal_json(stage="CHARGEBACK", status="UNDER_REVIEW", actions=("provide_supporting_info",))
    paypal = FakePayPal(chargeback)
    _, run = setup_case(paypal)

    approve(run, approved_by="seller", client=paypal)

    assert paypal.sent[0][0] == "provide_supporting_info"


def test_t23_double_approve_calls_paypal_once(app):
    paypal = FakePayPal(paypal_json(), paypal_json(status="WAITING_FOR_BUYER_RESPONSE", actions=()))
    _, run = setup_case(paypal)

    first = approve(run, approved_by="seller", client=paypal)
    second = approve(run, approved_by="seller", client=paypal)

    assert len(paypal.sent) == 1
    assert first.id == second.id
    assert Action.query.count() == 1


def test_stale_allowed_actions_are_rechecked_before_sending(app):
    paypal = FakePayPal(paypal_json())
    _, run = setup_case(paypal)
    paypal.state = paypal_json(status="WAITING_FOR_BUYER_RESPONSE", actions=("accept_claim",))

    with pytest.raises(ActionBlocked):
        approve(run, approved_by="seller", client=paypal)

    assert paypal.sent == []
    assert Action.query.count() == 0
    assert AuditLog.query.one().event == "action_blocked"


def test_t19_approve_without_approver_is_blocked(app):
    paypal = FakePayPal(paypal_json())
    _, run = setup_case(paypal)

    with pytest.raises(ActionBlocked):
        approve(run, approved_by=None, client=paypal)

    assert paypal.sent == []


def test_paypal_rejection_marks_action_failed_with_debug_id(app):
    paypal = RejectingPayPal(paypal_json())
    _, run = setup_case(paypal)

    action = approve(run, approved_by="seller", client=paypal)

    assert action.status == "failed"
    assert "dbg-422" in action.error


def test_no_response_from_paypal_marks_action_failed(app):
    import requests

    class SilentPayPal(FakePayPal):
        def provide_evidence(self, dispute_id, evidences, request_id=None):
            raise requests.Timeout("timed out")

    paypal = SilentPayPal(paypal_json())
    _, run = setup_case(paypal)

    action = approve(run, approved_by="seller", client=paypal)

    assert action.status == "failed"
    assert "No response from PayPal" in action.error
