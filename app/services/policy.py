"""Safety rules. Plain code checks every AI proposal; the AI can't talk its way past these."""
import re
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, timedelta, timezone
from decimal import Decimal

from app.labels import label

# The only actions a seller may take. PayPal also lists sandbox test tools
# (adjudicate, require_evidence); this app never offers those.
SELLER_ACTIONS = {"provide_evidence", "provide_supporting_info", "make_offer", "accept_claim"}
MONEY_ACTIONS = {"make_offer", "accept_claim"}
URGENT_HOURS = 48
NOT_WAITING_ON_SELLER = {"WAITING_FOR_BUYER_RESPONSE", "UNDER_REVIEW", "RESOLVED"}

# 10+ capitals/digits with at least 8 digits: looks like a tracking or transaction number
NUMBER_RE = re.compile(r"\b(?=(?:[A-Z]*\d){8})[A-Z0-9]{10,}\b")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
WRITTEN_DATE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}),? (\d{4})\b"
)
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
AMOUNT_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)|\b(\d[\d,]*\.\d{2})\s?USD\b")


def _cents(value):
    return Decimal(str(value).replace(",", "")).quantize(Decimal("0.01"))


@dataclass
class PolicyResult:
    allowed: bool
    action: str | None
    reasons: list = field(default_factory=list)

    def as_dict(self):
        return {"allowed": self.allowed, "action": self.action, "reasons": self.reasons}


def action_for(dispute, recommendation):
    """Rule 3: the right PayPal action for this recommendation at this stage."""
    if recommendation == "fight":
        return "provide_supporting_info" if dispute.stage == "CHARGEBACK" else "provide_evidence"
    return {"partial_refund": "make_offer", "full_refund": "accept_claim"}.get(recommendation)


def _known_records(dispute):
    """Every record ID, number and date that really exists for this dispute's order."""
    record_ids, numbers, dates = set(), set(), set()

    def add_date(value):
        if value:
            dates.add(value.date().isoformat())

    add_date(dispute.seller_response_due)
    order = dispute.order
    if order:
        record_ids.add(f"order:{order.id}")
        numbers.add(order.paypal_transaction_id)
        add_date(order.ordered_at)
        for s in order.shipments:
            record_ids.add(f"shipment:{s.id}")
            if s.tracking_number:
                numbers.add(s.tracking_number)
            add_date(s.shipped_at)
            add_date(s.delivered_at)
        for m in order.messages:
            record_ids.add(f"message:{m.id}")
            add_date(m.sent_at)
    return record_ids, numbers, dates


def review_proposal(dispute, proposal):
    """Check an AI proposal. Blocked proposals go to 'needs review', never to PayPal."""
    reasons = []
    rec = proposal.get("recommendation")

    # Rules 2 + 3: right action for the stage, and PayPal must allow it right now
    action = action_for(dispute, rec)
    if action is None or action not in SELLER_ACTIONS:
        reasons.append(f"Unknown recommendation {rec!r}")
    elif action not in (dispute.allowed_actions or []):
        allowed = ", ".join(label(a) for a in dispute.allowed_actions or []) or "nothing"
        reasons.append(f"PayPal doesn't currently allow you to {label(action)} on this dispute. "
                       f"Allowed now: {allowed}.")

    # Rule 1: no made-up evidence
    record_ids, numbers, dates = _known_records(dispute)
    evidence = proposal.get("evidence") or []
    for item in evidence:
        if item.get("record_id") not in record_ids:
            reasons.append(f"Evidence cites a record that doesn't exist for this order: {item.get('record_id')}")
    if rec == "fight" and not evidence:
        reasons.append("A 'fight' proposal needs at least one evidence record")

    text = " ".join([proposal.get("draft_note") or ""] + [i.get("claim") or "" for i in evidence])
    for number in dict.fromkeys(NUMBER_RE.findall(text)):
        if number not in numbers:
            reasons.append(f"Mentions number {number}, which isn't in the records")
    for found in DATE_RE.findall(text):
        if found not in dates:
            reasons.append(f"Mentions date {found}, which isn't in the records")
    for m in WRITTEN_DATE_RE.finditer(text):  # "October 26, 2026", "Oct 26 2026"
        try:
            iso = date_cls(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
        except ValueError:
            reasons.append(f"Mentions an impossible date: {m.group(0)}")
            continue
        if iso not in dates:
            reasons.append(f"Mentions date {m.group(0)}, which isn't in the records")

    order = dispute.order
    known_amounts = {
        _cents(v)
        for v in (dispute.amount, order.amount if order else None, proposal.get("offer_amount"))
        if v is not None
    }
    for m in AMOUNT_RE.finditer(text):
        if _cents(m.group(1) or m.group(2)) not in known_amounts:
            reasons.append(f"Mentions amount {m.group(0).strip()}, which isn't in the records")

    # Rule 4: an offer is positive and never more than the disputed amount
    offer = proposal.get("offer_amount")
    if rec == "partial_refund":
        if offer is None or offer <= 0:
            reasons.append("A partial refund needs a positive offer_amount")
        elif dispute.amount is None:
            reasons.append("Disputed amount unknown, so an offer can't be checked")
        elif Decimal(str(offer)) > Decimal(str(dispute.amount)):
            reasons.append(f"Offer {offer} is larger than the disputed amount {dispute.amount}")
    elif offer is not None:
        reasons.append("offer_amount must be empty unless offering a partial refund")

    reasons = list(dict.fromkeys(reasons))  # same problem mentioned twice -> one reason
    return PolicyResult(allowed=not reasons, action=action, reasons=reasons)


def check_execution(dispute, proposal, approved_by):
    """Final gate right before calling PayPal: all rules again, plus a person's approval."""
    result = review_proposal(dispute, proposal)
    if not approved_by:
        note = " (this moves money)" if result.action in MONEY_ACTIONS else ""
        result.reasons.append(f"Needs the seller's approval before anything is sent to PayPal{note}")
        result.allowed = False
    return result


def is_urgent(dispute, now=None, hours=URGENT_HOURS):
    """Under 48 hours to the deadline, or past it, while the seller still has to respond."""
    if dispute.status in NOT_WAITING_ON_SELLER:
        return False
    due = dispute.seller_response_due
    if due is None:
        return False
    now = now or datetime.now(timezone.utc)
    return due - now < timedelta(hours=hours)
