"""Display names for PayPal's dispute codes. Stored data keeps the raw codes."""

LABELS = {
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED": "Item not received",
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED": "Not as described",
    "UNAUTHORISED": "Unauthorized payment",
    "CREDIT_NOT_PROCESSED": "Credit not processed",
    "DUPLICATE_TRANSACTION": "Duplicate payment",
    "INCORRECT_AMOUNT": "Incorrect amount",
    "PAYMENT_BY_OTHER_MEANS": "Paid by other means",
    "CANCELED_RECURRING_BILLING": "Canceled recurring billing",
    "PROBLEM_WITH_REMITTANCE": "Problem with remittance",
    "OPEN": "Open",
    "WAITING_FOR_SELLER_RESPONSE": "Waiting on you",
    "WAITING_FOR_BUYER_RESPONSE": "Waiting on buyer",
    "UNDER_REVIEW": "Under review by PayPal",
    "RESOLVED": "Resolved",
    "INQUIRY": "Inquiry",
    "CHARGEBACK": "Chargeback",
    "PRE_ARBITRATION": "Pre-arbitration",
    "ARBITRATION": "Arbitration",
    "provide_evidence": "submit evidence",
    "provide_supporting_info": "send supporting info",
    "make_offer": "make an offer",
    "accept_claim": "accept the claim",
    "send_message": "send a message",
    "escalate": "escalate",
    "acknowledge_return_item": "acknowledge the return",
}


def label(code):
    if not code:
        return ""
    return LABELS.get(code, code.replace("_", " ").capitalize())
