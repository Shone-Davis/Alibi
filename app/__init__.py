import json
import os

import click
from dotenv import load_dotenv
from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()


def _database_url():
    url = os.environ["DATABASE_URL"]
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def create_app():
    load_dotenv()
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = _database_url()
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only")
    app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("RENDER"))
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = None

    from app import models  # noqa: F401  (so migrations see the tables)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(models.User, int(user_id))

    from app.paypal.client import PayPalClient
    app.extensions["paypal"] = PayPalClient()

    from app.routes.main import bp
    app.register_blueprint(bp)

    from app.paypal.webhooks import bp as webhooks_bp
    app.register_blueprint(webhooks_bp)
    csrf.exempt(webhooks_bp)  # PayPal can't send a CSRF token; the signature check protects it

    from app.routes.auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    @app.cli.command("sync")
    def sync_cmd():
        """Pull every dispute from PayPal and save it."""
        from app.services.sync import sync_all
        n = sync_all(app.extensions["paypal"])
        print(f"Synced {n} dispute(s)")

    @app.cli.command("seed")
    def seed_cmd():
        """Create fake shop orders for disputes that don't have one yet."""
        from app.demo_seed import seed_orders
        created = seed_orders()
        for dispute_id, scenario in created:
            print(f"  {dispute_id}: {scenario}")
        print(f"Seeded {len(created)} order(s)")

    @app.cli.command("analyze")
    @click.argument("dispute_id")
    def analyze_cmd(dispute_id):
        """Run the AI on one dispute and print its proposal."""
        from app.models import Dispute
        from app.services.agent import run_agent
        d = Dispute.query.filter_by(paypal_dispute_id=dispute_id).first()
        if d is None:
            print("Not in the database. Run: flask --app run sync")
            return
        run = run_agent(d)
        print(f"Status: {run.status}")
        if run.policy:
            print(f"Action: {run.policy['action']}  allowed: {run.policy['allowed']}")
        if run.error:
            print(f"Error: {run.error}")
        if run.proposal:
            print(json.dumps(run.proposal, indent=2))

    @app.cli.command("approve")
    @click.argument("dispute_id")
    def approve_cmd(dispute_id):
        """Approve the latest proposal that passed the safety rules and send it to PayPal."""
        from app.models import AgentRun, Dispute
        from app.services.actions import ActionBlocked, approve
        d = Dispute.query.filter_by(paypal_dispute_id=dispute_id).first()
        run = d and AgentRun.query.filter_by(dispute_id=d.id, status="ok").order_by(AgentRun.id.desc()).first()
        if not run:
            print(f"No proposal has passed the safety rules. Run: flask --app run analyze {dispute_id}")
            return
        try:
            action = approve(run, approved_by="seller:cli", client=app.extensions["paypal"])
        except ActionBlocked as e:
            print("Blocked: " + "; ".join(e.reasons))
            return
        print(f"{action.action_type}: {action.status}")
        if action.error:
            print(action.error)
        print(f"Dispute status: {d.status}")

    @app.cli.command("create-user")
    @click.argument("email")
    @click.password_option()
    def create_user_cmd(email, password):
        """Create a seller login, or reset its password."""
        from app.models import User
        email = email.strip().lower()
        user = User.query.filter_by(email=email).first() or User(email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Saved login for {email}")

    @app.cli.command("ensure-demo-user")
    def ensure_demo_user_cmd():
        """Create the judges' demo login from DEMO_USER_EMAIL and DEMO_USER_PASSWORD, if set."""
        from app.models import User
        email = os.environ.get("DEMO_USER_EMAIL", "").strip().lower()
        password = os.environ.get("DEMO_USER_PASSWORD", "")
        if not email or not password:
            print("DEMO_USER_EMAIL or DEMO_USER_PASSWORD not set; skipping")
            return
        user = User.query.filter_by(email=email).first() or User(email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Demo login ready for {email}")

    return app
