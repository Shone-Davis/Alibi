from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.models import User

bp = Blueprint("auth", __name__)


def _safe_next(target):
    """Only follow redirects back into this site."""
    target = target or ""
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or "\\" in target:
        return url_for("main.index")
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(request.form.get("password", "")):
            login_user(user)
            return redirect(_safe_next(request.args.get("next")))
        flash("Email or password is incorrect.", "error")
    return render_template("login.html")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
