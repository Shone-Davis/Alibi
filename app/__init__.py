import os

from dotenv import load_dotenv
from flask import Flask
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
migrate = Migrate()


def _database_url():
    url = os.environ["DATABASE_URL"]
    # Render gives postgres:// or postgresql://; SQLAlchemy needs the psycopg driver named
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def create_app():
    load_dotenv()
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = _database_url()
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only")

    db.init_app(app)
    migrate.init_app(app, db)

    from app import models  # noqa: F401  (so migrations see the tables)

    from app.paypal.client import PayPalClient
    app.extensions["paypal"] = PayPalClient()

    from app.routes.main import bp
    app.register_blueprint(bp)

    from app.paypal.webhooks import bp as webhooks_bp
    app.register_blueprint(webhooks_bp)

    @app.cli.command("sync")
    def sync_cmd():
        """Pull every dispute from PayPal and save it."""
        from app.services.sync import sync_all
        n = sync_all(app.extensions["paypal"])
        print(f"Synced {n} dispute(s)")

    return app
