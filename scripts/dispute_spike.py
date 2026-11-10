"""
PayPal Disputes sandbox spike.

Proves the full dispute lifecycle works in sandbox before building anything:
  order -> buyer pays -> buyer opens dispute -> escalate -> require evidence
  -> submit evidence -> adjudicate (sandbox only)

Setup:
  pip install requests python-dotenv
  Create a .env file next to this script (never commit it):
      PAYPAL_CLIENT_ID=
      PAYPAL_CLIENT_SECRET=

Usage:
  python dispute_spike.py token                 # check auth + dispute scopes
  python dispute_spike.py order 25.00           # create order, prints approve link
  python dispute_spike.py capture ORDER_ID      # capture after buyer approves
  python dispute_spike.py list                  # list disputes on your account
  python dispute_spike.py show DISPUTE_ID       # status, stage, reason, available actions
  python dispute_spike.py escalate DISPUTE_ID   # inquiry -> claim
  python dispute_spike.py require DISPUTE_ID    # sandbox: UNDER_REVIEW -> WAITING_FOR_SELLER_RESPONSE
  python dispute_spike.py evidence DISPUTE_ID [path/to/proof.pdf]
  python dispute_spike.py adjudicate DISPUTE_ID SELLER_FAVOR|BUYER_FAVOR   # sandbox only
"""

import json
import os
import sys

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID")
CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET")
BASE = os.environ.get("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")


def die(msg):
    print(f"\n[FAIL] {msg}")
    sys.exit(1)


def get_token(verbose=False):
    if not CLIENT_ID or not CLIENT_SECRET:
        die("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env")
    r = requests.post(
        f"{BASE}/v1/oauth2/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    if r.status_code != 200:
        die(f"Auth failed ({r.status_code}): {r.text}")
    body = r.json()
    if verbose:
        scopes = body.get("scope", "").split()
        dispute_scopes = [s for s in scopes if "disputes" in s]
        print(f"[OK] Got token. {len(scopes)} scopes total.")
        if dispute_scopes:
            print("[OK] Dispute scopes present:")
            for s in dispute_scopes:
                print("     ", s)
        else:
            print(
                "[WARN] No dispute scopes. In the Developer Dashboard, open your app ->\n"
                "       Features -> enable 'Disputes', save, then run this again."
            )
    return body["access_token"]


def headers(token, json_body=True):
    h = {"Authorization": f"Bearer {token}"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def show_response(r, ok_codes=(200, 201, 202, 204)):
    print(f"HTTP {r.status_code}")
    try:
        print(json.dumps(r.json(), indent=2))
    except ValueError:
        print(r.text or "(empty body)")
    if r.status_code not in ok_codes:
        die("Request failed (see body above; debug_id helps in the PayPal Discord)")


def link_names(dispute):
    return [l.get("rel") for l in dispute.get("links", [])]




def cmd_token():
    get_token(verbose=True)


def cmd_order(amount="25.00"):
    token = get_token()
    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "description": "Spike test item",
                "amount": {"currency_code": "USD", "value": amount},
            }
        ],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "return_url": "https://example.com/return",
                    "cancel_url": "https://example.com/cancel",
                }
            }
        },
    }
    r = requests.post(
        f"{BASE}/v2/checkout/orders", headers=headers(token), json=body, timeout=30
    )
    show_response(r)
    data = r.json()
    approve = next(
        (l["href"] for l in data.get("links", []) if l["rel"] in ("payer-action", "approve")),
        None,
    )
    print(f"\nORDER_ID: {data['id']}")
    print(f"Open this, log in as your SANDBOX PERSONAL (buyer) account, approve:\n{approve}")
    print("You'll land on example.com afterwards. That's expected. Then run: capture ORDER_ID")


def cmd_capture(order_id):
    token = get_token()
    r = requests.post(
        f"{BASE}/v2/checkout/orders/{order_id}/capture",
        headers=headers(token),
        timeout=30,
    )
    show_response(r)
    cap = r.json()["purchase_units"][0]["payments"]["captures"][0]
    print(f"\nCAPTURE (transaction) ID: {cap['id']}  status: {cap['status']}")
    print(
        "Next: log into https://www.sandbox.paypal.com as the BUYER, find this payment\n"
        "in Activity, and report a problem (e.g. 'item not received')."
    )


def cmd_list():
    token = get_token()
    r = requests.get(
        f"{BASE}/v1/customer/disputes", headers=headers(token), params={"page_size": 10}, timeout=30
    )
    show_response(r)
    items = r.json().get("items", [])
    print(f"\nFound {len(items)} dispute(s).")
    for d in items:
        print(f"  {d['dispute_id']}  {d.get('reason')}  {d.get('status')}")
    if not items:
        print("New sandbox disputes can take several minutes to show up. Try again shortly.")


def cmd_show(dispute_id):
    token = get_token()
    r = requests.get(
        f"{BASE}/v1/customer/disputes/{dispute_id}", headers=headers(token), timeout=30
    )
    show_response(r)
    d = r.json()
    print("\n--- SUMMARY ---")
    print("status:     ", d.get("status"))
    print("stage:      ", d.get("dispute_life_cycle_stage"))
    print("reason:     ", d.get("reason"))
    print("amount:     ", d.get("dispute_amount"))
    print("respond by: ", d.get("seller_response_due_date"))
    print("actions now:", link_names(d))


def cmd_escalate(dispute_id):
    token = get_token()
    r = requests.post(
        f"{BASE}/v1/customer/disputes/{dispute_id}/escalate",
        headers=headers(token),
        json={"note": "Escalating to claim for sandbox lifecycle test."},
        timeout=30,
    )
    show_response(r)


def cmd_require(dispute_id):
    token = get_token()
    r = requests.post(
        f"{BASE}/v1/customer/disputes/{dispute_id}/require-evidence",
        headers=headers(token),
        json={"action": "SELLER_EVIDENCE"},
        timeout=30,
    )
    show_response(r)


def cmd_evidence(dispute_id, file_path=None):
    token = get_token()
    payload = {
        "evidences": [
            {
                "evidence_type": "PROOF_OF_FULFILLMENT",
                "evidence_info": {
                    "tracking_info": [
                        {"carrier_name": "USPS", "tracking_number": "9400111899223197428490"}
                    ]
                },
                "notes": "Order shipped via USPS and delivered. Tracking attached.",
            }
        ]
    }
    files = {"input": (None, json.dumps(payload), "application/json")}
    handle = None
    if file_path:
        handle = open(file_path, "rb")
        files["evidence-file"] = (os.path.basename(file_path), handle, "application/pdf")
    try:
        r = requests.post(
            f"{BASE}/v1/customer/disputes/{dispute_id}/provide-evidence",
            headers=headers(token, json_body=False),  # requests sets multipart boundary
            files=files,
            timeout=60,
        )
    finally:
        if handle:
            handle.close()
    show_response(r)


def cmd_adjudicate(dispute_id, outcome="SELLER_FAVOR"):
    token = get_token()
    r = requests.post(
        f"{BASE}/v1/customer/disputes/{dispute_id}/adjudicate",
        headers=headers(token),
        json={"adjudication_outcome": outcome},
        timeout=30,
    )
    show_response(r)


COMMANDS = {
    "token": cmd_token,
    "order": cmd_order,
    "capture": cmd_capture,
    "list": cmd_list,
    "show": cmd_show,
    "escalate": cmd_escalate,
    "require": cmd_require,
    "evidence": cmd_evidence,
    "adjudicate": cmd_adjudicate,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0)
    COMMANDS[sys.argv[1]](*sys.argv[2:])
