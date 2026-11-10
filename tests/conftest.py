from app import create_app, db
import pytest
import os

os.environ["DATABASE_URL"] = "postgresql+psycopg://localhost/alibi_test"
os.environ["PAYPAL_CLIENT_ID"] = "test-id"
os.environ["PAYPAL_CLIENT_SECRET"] = "test-secret"
os.environ["PAYPAL_BASE_URL"] = "https://api.test.paypal"


@pytest.fixture
def app():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    from app import limiter
    limiter.reset()
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def seller(app):
    from app.models import User

    user = User(email="seller@example.com")
    user.set_password("correct-horse")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def client(app, seller):
    c = app.test_client()
    c.post("/login", data={"email": "seller@example.com", "password": "correct-horse"})
    return c
