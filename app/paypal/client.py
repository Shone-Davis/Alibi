import json
import logging
import os
import time
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)


class PayPalError(Exception):
    def __init__(self, status, message, debug_id=None):
        super().__init__(f"PayPal {status}: {message} (debug_id={debug_id})")
        self.status = status
        self.debug_id = debug_id


class PayPalClient:
    def __init__(self, client_id=None, client_secret=None, base_url=None):
        self.client_id = client_id or os.environ["PAYPAL_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["PAYPAL_CLIENT_SECRET"]
        self.base_url = base_url or os.environ.get(
            "PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com"
        )
        self._token = None
        self._token_expires_at = 0

    # token

    def _get_token(self):
        if self._token and time.time() < self._token_expires_at:
            return self._token
        r = requests.post(
            f"{self.base_url}/v1/oauth2/token",
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        if r.status_code != 200:
            raise self._error(r)
        body = r.json()
        self._token = body["access_token"]
        # refresh 5 minutes before PayPal says it expires
        self._token_expires_at = time.time() + body.get("expires_in", 3600) - 300
        return self._token

    #  every API call goes through here

    def _request(self, method, path, headers=None, **kwargs):
        for attempt in (1, 2):
            h = {"Authorization": f"Bearer {self._get_token()}"}
            if headers:
                h.update(headers)
            r = requests.request(
                method, f"{self.base_url}{path}", headers=h, timeout=30, **kwargs
            )
            if r.status_code == 401 and attempt == 1:
                self._token = None  # force a fresh token, then retry once
                continue
            if r.status_code >= 400:
                raise self._error(r)
            return r.json() if r.content else {}

    @staticmethod
    def _error(r):
        try:
            body = r.json()
        except ValueError:
            body = {}
        debug_id = body.get("debug_id") or r.headers.get("Paypal-Debug-Id")
        msg = body.get("message") or body.get(
            "error_description") or r.text[:200]
        log.error("PayPal error %s: %s (debug_id=%s)",
                  r.status_code, msg, debug_id)
        return PayPalError(r.status_code, msg, debug_id)

    # disputes

    def list_disputes(self, page_size=50, max_pages=20):
        """Every dispute on the account, following PayPal's next-page links."""
        items, path, params = [], "/v1/customer/disputes", {"page_size": page_size}
        for _ in range(max_pages):
            data = self._request("GET", path, params=params)
            items.extend(data.get("items", []))
            next_href = next((l["href"] for l in data.get("links", []) if l.get("rel") == "next"), None)
            if not next_href:
                break
            parts = urlsplit(next_href)
            path, params = parts.path + (f"?{parts.query}" if parts.query else ""), None
        return items

    def get_dispute(self, dispute_id):
        return self._request("GET", f"/v1/customer/disputes/{dispute_id}")

    def verify_webhook_signature(self, sig, raw_body, webhook_id):
        """Ask PayPal whether this webhook really came from PayPal."""
        import json
        fields = {**sig, "webhook_id": webhook_id}
        # insert the event body exactly as PayPal sent it; re encoding it can break the check
        body = json.dumps(fields)[:-1] + ',"webhook_event":' + raw_body + "}"
        result = self._request(
            "POST",
            "/v1/notifications/verify-webhook-signature",
            headers={"Content-Type": "application/json"},
            data=body,
        )
        return result.get("verification_status") == "SUCCESS"

    @staticmethod
    def _request_id(request_id):
        return {"PayPal-Request-Id": request_id} if request_id else {}

    def provide_evidence(self, dispute_id, evidences, request_id=None):
        body = json.dumps({"evidences": evidences})
        return self._request(
            "POST",
            f"/v1/customer/disputes/{dispute_id}/provide-evidence",
            headers=self._request_id(request_id),
            files={"input": (None, body, "application/json")},
        )

    def provide_supporting_info(self, dispute_id, notes, request_id=None):
        body = json.dumps({"notes": notes})
        return self._request(
            "POST",
            f"/v1/customer/disputes/{dispute_id}/provide-supporting-info",
            headers=self._request_id(request_id),
            files={"input": (None, body, "application/json")},
        )

    def make_offer(self, dispute_id, note, amount, currency, request_id=None):
        return self._request(
            "POST",
            f"/v1/customer/disputes/{dispute_id}/make-offer",
            headers=self._request_id(request_id),
            json={
                "note": note,
                "offer_type": "REFUND",
                "offer_amount": {"currency_code": currency, "value": f"{float(amount):.2f}"},
            },
        )

    def accept_claim(self, dispute_id, note, request_id=None):
        return self._request(
            "POST",
            f"/v1/customer/disputes/{dispute_id}/accept-claim",
            headers=self._request_id(request_id),
            json={"note": note},
        )
