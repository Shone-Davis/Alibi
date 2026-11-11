from app import db
from app.models import Action, AgentRun, Dispute, Order, Shipment

TRACKING = "9400111222333444555666"


class FakePayPal:
    def __init__(self, allowed):
        self.allowed = allowed
        self.sent = []

    def get_dispute(self, dispute_id):
        return {
            "dispute_id": dispute_id,
            "status": "UNDER_REVIEW" if self.sent else "WAITING_FOR_SELLER_RESPONSE",
            "dispute_life_cycle_stage": "INQUIRY",
            "dispute_amount": {"currency_code": "USD", "value": "25.00"},
            "disputed_transactions": [{"seller_transaction_id": "TXN1"}],
            "links": [] if self.sent else [{"rel": a} for a in self.allowed],
        }

    def provide_evidence(self, dispute_id, evidences, request_id=None):
        self.sent.append(request_id)
        return {}


def make_case(allowed=("provide_evidence",)):
    order = Order(paypal_transaction_id="TXN1", amount=25, currency="USD")
    db.session.add(order)
    db.session.flush()
    ship = Shipment(order_id=order.id, carrier="USPS", tracking_number=TRACKING)
    db.session.add(ship)
    d = Dispute(paypal_dispute_id="PP-D-1", stage="INQUIRY", amount=25, currency="USD",
                status="WAITING_FOR_SELLER_RESPONSE", allowed_actions=list(allowed),
                seller_transaction_id="TXN1", order_id=order.id)
    db.session.add(d)
    db.session.flush()
    run = AgentRun(
        dispute_id=d.id,
        status="ok",
        proposal={
            "recommendation": "fight",
            "confidence": 0.9,
            "reasoning": "Delivered.",
            "evidence": [{"record_id": f"shipment:{ship.id}", "claim": "Delivered"}],
            "offer_amount": None,
            "draft_note": f"The seller shipped via USPS, tracking {TRACKING}.",
        },
        policy={"allowed": True, "action": "provide_evidence", "reasons": []},
    )
    db.session.add(run)
    db.session.commit()
    return d, run


def test_t25_approve_button_shown_for_allowed_action(app, client):
    make_case()
    page = client.get("/disputes/PP-D-1")

    assert page.status_code == 200
    assert b'id="approve-button"' in page.data


def test_t25_approve_button_hidden_when_paypal_no_longer_allows_it(app, client):
    make_case(allowed=("accept_claim",))
    page = client.get("/disputes/PP-D-1")

    assert b'id="approve-button"' not in page.data


def test_approve_twice_from_page_sends_once(app, client):
    _, run = make_case()
    fake = FakePayPal(["provide_evidence"])
    app.extensions["paypal"] = fake

    client.post("/disputes/PP-D-1/approve", data={"run_id": run.id})
    client.post("/disputes/PP-D-1/approve", data={"run_id": run.id})

    assert len(fake.sent) == 1
    assert Action.query.count() == 1
    assert b'id="approve-button"' not in client.get("/disputes/PP-D-1").data


def test_unknown_dispute_is_404(app, client):
    assert client.get("/disputes/PP-NOPE").status_code == 404


def test_t24_dashboard_sorted_by_deadline_with_urgent_flagged(app, client):
    from datetime import datetime, timedelta, timezone

    from app.services.dashboard import dashboard_rows

    now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    for pid, hours, status in [
        ("PP-LATER", 72, "WAITING_FOR_SELLER_RESPONSE"),
        ("PP-NONE", None, "WAITING_FOR_SELLER_RESPONSE"),
        ("PP-SOON", 10, "WAITING_FOR_SELLER_RESPONSE"),
        ("PP-BUYER", 5, "WAITING_FOR_BUYER_RESPONSE"),
    ]:
        due = now + timedelta(hours=hours) if hours is not None else None
        db.session.add(Dispute(paypal_dispute_id=pid, status=status, amount=10, seller_response_due=due))
    db.session.commit()

    rows, summary = dashboard_rows(now=now)

    assert [r["id"] for r in rows] == ["PP-BUYER", "PP-SOON", "PP-LATER", "PP-NONE"]
    assert {r["id"]: r["urgent"] for r in rows} == {
        "PP-BUYER": False, "PP-SOON": True, "PP-LATER": False, "PP-NONE": False,
    }
    assert summary["urgent"] == 1
    assert summary["waiting_on_you"] == 3


def test_t24_dashboard_page_renders_grid(app, client):
    make_case()
    page = client.get("/")

    assert page.status_code == 200
    assert b'id="grid"' in page.data
    assert b"PP-D-1" in page.data


def test_settings_saved_and_audited(app, client):
    from app.models import AuditLog
    from app.services.settings import get_settings

    r = client.post("/settings", data={"refund_threshold": "20", "urgent_hours": "504"})

    assert r.status_code == 302
    s = get_settings()
    assert (str(s.refund_threshold), s.urgent_hours) == ("20.00", 504)
    assert AuditLog.query.one().event == "settings_changed"


def test_invalid_settings_rejected(app, client):
    from app.services.settings import get_settings

    for bad in ({"refund_threshold": "-5", "urgent_hours": "48"},
                {"refund_threshold": "NaN", "urgent_hours": "48"},
                {"refund_threshold": "15", "urgent_hours": "0"},
                {"refund_threshold": "abc", "urgent_hours": "48"}):
        client.post("/settings", data=bad)

    s = get_settings()
    assert (str(s.refund_threshold), s.urgent_hours) == ("15.00", 48)


def test_urgency_window_comes_from_settings(app, client):
    from datetime import datetime, timedelta, timezone

    from app.services.dashboard import dashboard_rows

    now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    db.session.add(Dispute(paypal_dispute_id="PP-TEN-DAYS", status="WAITING_FOR_SELLER_RESPONSE",
                           amount=10, seller_response_due=now + timedelta(days=10)))
    db.session.commit()

    assert dashboard_rows(now=now)[0][0]["urgent"] is False
    client.post("/settings", data={"refund_threshold": "15", "urgent_hours": "504"})
    assert dashboard_rows(now=now)[0][0]["urgent"] is True


def test_sync_button_pulls_disputes_from_paypal(app, client):
    class ListingPayPal:
        def list_disputes(self, page_size=50):
            return [{"dispute_id": "PP-NEW"}]

        def get_dispute(self, dispute_id):
            return {"dispute_id": dispute_id, "status": "WAITING_FOR_SELLER_RESPONSE", "links": []}

    app.extensions["paypal"] = ListingPayPal()

    r = client.post("/sync")

    assert r.status_code == 302
    assert Dispute.query.filter_by(paypal_dispute_id="PP-NEW").count() == 1
