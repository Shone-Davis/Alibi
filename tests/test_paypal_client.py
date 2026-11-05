import logging

import pytest
import responses

from app.paypal.client import PayPalClient, PayPalError

BASE = "https://api.test.paypal"
TOKEN_URL = f"{BASE}/v1/oauth2/token"
DISPUTES_URL = f"{BASE}/v1/customer/disputes"


def make_client():
    # fake credentials; no .env needed
    return PayPalClient("test-id", "test-secret", BASE)


def add_token(token="tok-1", expires_in=32400):
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"access_token": token, "expires_in": expires_in},
        status=200,
    )


def count_calls(url):
    return sum(1 for c in responses.calls if c.request.url.startswith(url))


# T1: token is cached

@responses.activate
def test_t1_two_calls_reuse_one_token():
    add_token()
    responses.add(responses.GET, DISPUTES_URL, json={"items": []}, status=200)

    client = make_client()
    client.list_disputes()
    client.list_disputes()

    assert count_calls(TOKEN_URL) == 1
    assert count_calls(DISPUTES_URL) == 2


# T2: expired token or 401 -> new token, retry once
@responses.activate
def test_t2_expired_token_gets_refreshed():
    add_token("tok-1")
    add_token("tok-2")
    responses.add(responses.GET, DISPUTES_URL, json={"items": []}, status=200)

    client = make_client()
    client.list_disputes()
    client._token_expires_at = 0  # pretend time ran out
    client.list_disputes()

    assert count_calls(TOKEN_URL) == 2
    last = responses.calls[-1].request
    assert last.headers["Authorization"] == "Bearer tok-2"


@responses.activate
def test_t2_401_refreshes_and_retries_once():
    add_token("tok-1")
    add_token("tok-2")
    # first disputes call is rejected, second succeeds
    responses.add(responses.GET, DISPUTES_URL, json={
                  "message": "expired"}, status=401)
    responses.add(
        responses.GET,
        DISPUTES_URL,
        json={"items": [{"dispute_id": "PP-D-1"}]},
        status=200,
    )

    items = make_client().list_disputes()

    assert items == [{"dispute_id": "PP-D-1"}]
    assert count_calls(TOKEN_URL) == 2
    assert count_calls(DISPUTES_URL) == 2


@responses.activate
def test_t2_401_twice_gives_up_instead_of_looping():
    add_token("tok-1")
    add_token("tok-2")
    responses.add(responses.GET, DISPUTES_URL, json={
                  "message": "nope"}, status=401)

    with pytest.raises(PayPalError) as exc:
        make_client().list_disputes()

    assert exc.value.status == 401
    assert count_calls(DISPUTES_URL) == 2


# T3: 4xx error carries debug_id and is logged

@responses.activate
def test_t3_4xx_raises_with_debug_id_and_logs(caplog):
    add_token()
    responses.add(
        responses.GET,
        DISPUTES_URL,
        json={"name": "INVALID_REQUEST",
              "message": "Bad request", "debug_id": "dbg123"},
        status=400,
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(PayPalError) as exc:
            make_client().list_disputes()

    assert exc.value.status == 400
    assert exc.value.debug_id == "dbg123"
    assert "dbg123" in caplog.text
