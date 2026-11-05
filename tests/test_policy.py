from datetime import datetime, timedelta, timezone

from app import db
from app.models import Dispute, Message, Order, Shipment
from app.services.policy import check_execution, is_urgent, review_proposal

TRACKING = "9400111222333444555666"
SHIPPED = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
DELIVERED = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def make_case(allowed=("provide_evidence",), stage="INQUIRY", amount=25, due=None):
    order = Order(paypal_transaction_id="TXN1", item_description="Mug", amount=amount,
                  currency="USD", ordered_at=SHIPPED - timedelta(days=2))
    db.session.add(order)
    db.session.flush()
    ship = Shipment(order_id=order.id, carrier="USPS", tracking_number=TRACKING,
                    shipped_at=SHIPPED, delivered_at=DELIVERED)
    db.session.add(ship)
    db.session.add(Message(order_id=order.id, sender="buyer",
                   body="hi", sent_at=SHIPPED))
    d = Dispute(paypal_dispute_id="PP-D-1", stage=stage, amount=amount, currency="USD",
                allowed_actions=list(allowed), order_id=order.id, seller_response_due=due)
    db.session.add(d)
    db.session.commit()
    return d, ship


def fight(ship, **overrides):
    p = {
        "recommendation": "fight",
        "confidence": 0.9,
        "reasoning": "Delivered.",
        "evidence": [{"record_id": f"shipment:{ship.id}",
                      "claim": f"USPS {TRACKING} was delivered on 2026-10-30."}],
        "offer_amount": None,
        "draft_note": f"The seller shipped via USPS on 2026-10-26, tracking {TRACKING}, delivered 2026-10-30.",
    }
    p.update(overrides)
    return p


def refund(kind, offer=None):
    return {"recommendation": kind, "confidence": 0.7, "reasoning": "x", "evidence": [],
            "offer_amount": offer, "draft_note": "The seller is offering a refund."}


def test_clean_fight_passes(app):
    d, ship = make_case()
    r = review_proposal(d, fight(ship))

    assert r.allowed, r.reasons
    assert r.action == "provide_evidence"


# T16: no made-up evidence

def test_t16_unknown_record_id_blocked(app):
    d, ship = make_case()
    r = review_proposal(
        d, fight(ship, evidence=[{"record_id": "shipment:999", "claim": "Delivered"}]))

    assert not r.allowed
    assert any("shipment:999" in x for x in r.reasons)


def test_t16_made_up_tracking_number_in_text_blocked(app):
    d, ship = make_case()
    r = review_proposal(
        d, fight(ship, draft_note="Delivered under tracking 9400999999999999999999."))

    assert not r.allowed
    assert any("9400999999999999999999" in x for x in r.reasons)


def test_t16_made_up_date_blocked(app):
    d, ship = make_case()
    r = review_proposal(d, fight(
        ship, draft_note=f"Tracking {TRACKING} shows delivery on 2026-10-29."))

    assert not r.allowed
    assert any("2026-10-29" in x for x in r.reasons)


# T17: only allowed actions, right one for the stage

def test_t17_action_not_allowed_by_paypal_blocked(app):
    d, ship = make_case(allowed=("accept_claim",))
    r = review_proposal(d, fight(ship))

    assert not r.allowed
    assert any("provide_evidence" in x for x in r.reasons)


def test_t17_chargeback_stage_uses_supporting_info(app):
    d, ship = make_case(
        allowed=("provide_supporting_info",), stage="CHARGEBACK")
    r = review_proposal(d, fight(ship))

    assert r.allowed, r.reasons
    assert r.action == "provide_supporting_info"


# T18: offers can't exceed the disputed amount

def test_t18_offer_above_disputed_amount_blocked(app):
    d, _ = make_case(allowed=("make_offer",))
    r = review_proposal(d, refund("partial_refund", offer=30))

    assert not r.allowed


def test_t18_offer_within_amount_allowed(app):
    d, _ = make_case(allowed=("make_offer",))
    r = review_proposal(d, refund("partial_refund", offer=10))

    assert r.allowed, r.reasons
    assert r.action == "make_offer"


def test_t15_injected_500_refund_still_blocked_by_rules(app):
    d, _ = make_case(allowed=("make_offer", "accept_claim"))
    r = review_proposal(d, refund("partial_refund", offer=500))

    assert not r.allowed


# T19: nothing goes to PayPal without the seller

def test_t19_money_action_without_approval_blocked(app):
    d, _ = make_case(allowed=("accept_claim",))
    r = check_execution(d, refund("full_refund"), approved_by=None)

    assert not r.allowed
    assert any("approval" in x for x in r.reasons)


def test_t19_money_action_with_approval_allowed(app):
    d, _ = make_case(allowed=("accept_claim",))
    r = check_execution(d, refund("full_refund"), approved_by="seller")

    assert r.allowed, r.reasons


# T20: deadline under 48 hours is urgent

def test_t20_under_48_hours_is_urgent(app):
    d, _ = make_case(due=NOW + timedelta(hours=47))
    assert is_urgent(d, now=NOW)


def test_t20_over_48_hours_or_no_deadline_not_urgent(app):
    d, _ = make_case(due=NOW + timedelta(hours=72))
    assert not is_urgent(d, now=NOW)

    d.seller_response_due = None
    assert not is_urgent(d, now=NOW)


def test_t16_made_up_written_date_blocked(app):
    d, ship = make_case()
    r = review_proposal(d, fight(
        ship, draft_note=f"Tracking {TRACKING} shows delivery on October 29, 2026."))

    assert not r.allowed
    assert any("October 29, 2026" in x for x in r.reasons)


def test_t16_correct_written_date_allowed(app):
    d, ship = make_case()
    r = review_proposal(d, fight(
        ship, draft_note=f"Tracking {TRACKING} shows delivery on Oct 30 2026."))

    assert r.allowed, r.reasons
