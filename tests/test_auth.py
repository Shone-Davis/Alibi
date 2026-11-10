from app.models import Action


def test_t26_logged_out_visitor_redirected_to_login(app):
    for path in ("/", "/disputes/PP-D-1"):
        r = app.test_client().get(path)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]


def test_t26_logged_out_approve_does_nothing(app):
    r = app.test_client().post("/disputes/PP-D-1/approve", data={"run_id": 1})

    assert r.status_code == 302
    assert Action.query.count() == 0


def test_wrong_password_rejected(app, seller):
    r = app.test_client().post("/login", data={"email": "seller@example.com", "password": "wrong"})

    assert r.status_code == 200
    assert b"incorrect" in r.data


def test_correct_login_reaches_dashboard(app, seller):
    c = app.test_client()
    r = c.post("/login", data={"email": "Seller@Example.com ", "password": "correct-horse"})

    assert r.status_code == 302
    assert c.get("/").status_code == 200


def test_login_never_redirects_off_site(app, seller):
    for target in ("//evil.example", "https://evil.example", "/\\evil.example"):
        r = app.test_client().post(
            f"/login?next={target}", data={"email": "seller@example.com", "password": "correct-horse"}
        )
        assert r.headers["Location"] == "/"


def test_webhook_reachable_without_login(app):
    assert app.test_client().get("/webhooks/paypal").status_code == 405


def test_forms_require_csrf_token(app, client):
    app.config["WTF_CSRF_ENABLED"] = True

    r = client.post("/disputes/PP-D-1/analyze")

    assert r.status_code == 400


def test_login_attempts_are_rate_limited(app, seller):
    c = app.test_client()
    codes = [
        c.post("/login", data={"email": "seller@example.com", "password": "wrong"}).status_code
        for _ in range(11)
    ]

    assert codes[:10] == [200] * 10
    assert codes[10] == 429
