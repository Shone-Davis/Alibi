from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.labels import label
from app.models import Action, AgentRun, AuditLog, Dispute
from app.services.actions import ActionBlocked, approve
from app.services.agent import run_agent
from app.services.dashboard import dashboard_rows
from app.services.policy import SELLER_ACTIONS, is_urgent

bp = Blueprint("main", __name__)
bp.add_app_template_filter(label, "label")


@bp.before_request
@login_required
def require_login():
    pass

ACTION_LABELS = {
    "provide_evidence": "Submit evidence to PayPal",
    "provide_supporting_info": "Send supporting info to PayPal",
    "make_offer": "Send partial refund offer",
    "accept_claim": "Accept claim and refund in full",
}


@bp.app_template_filter("when")
def when(value):
    return value.strftime("%b %-d, %Y %-I:%M %p %Z") if value else "Not set"


def _dispute_or_404(pid):
    dispute = Dispute.query.filter_by(paypal_dispute_id=pid).first()
    if dispute is None:
        abort(404)
    return dispute


@bp.route("/")
def index():
    rows, summary = dashboard_rows()
    return render_template("index.html", rows=rows, summary=summary)


@bp.route("/disputes/<pid>")
def dispute_detail(pid):
    dispute = _dispute_or_404(pid)
    run = AgentRun.query.filter_by(dispute_id=dispute.id).order_by(AgentRun.id.desc()).first()
    sent = Action.query.filter_by(agent_run_id=run.id).first() if run else None
    action = run.policy["action"] if run and run.policy else None
    can_approve = (
        run is not None
        and run.status == "ok"
        and sent is None
        and action in SELLER_ACTIONS
        and action in (dispute.allowed_actions or [])
    )
    timeline = AuditLog.query.filter_by(dispute_id=dispute.id).order_by(AuditLog.created_at).all()
    return render_template(
        "dispute.html",
        d=dispute,
        run=run,
        sent=sent,
        action=action,
        can_approve=can_approve,
        timeline=timeline,
        urgent=is_urgent(dispute),
        labels=ACTION_LABELS,
    )


@bp.post("/disputes/<pid>/analyze")
def analyze(pid):
    dispute = _dispute_or_404(pid)
    run = run_agent(dispute)
    if run.status == "ok":
        flash("Recommendation ready for your review.", "success")
    else:
        flash(run.error or "The recommendation needs review.", "error")
    return redirect(url_for("main.dispute_detail", pid=pid))


@bp.post("/disputes/<pid>/approve")
def approve_action(pid):
    dispute = _dispute_or_404(pid)
    run_id = request.form.get("run_id", type=int)
    run = AgentRun.query.filter_by(id=run_id, dispute_id=dispute.id).first_or_404()
    try:
        action = approve(run, approved_by=current_user.email, client=current_app.extensions["paypal"])
    except ActionBlocked as e:
        flash("Blocked: " + "; ".join(e.reasons), "error")
    else:
        if action.status == "sent":
            flash(f"Sent to PayPal. Dispute status is now: {label(dispute.status)}.", "success")
        else:
            flash(f"PayPal rejected the action: {action.error}", "error")
    return redirect(url_for("main.dispute_detail", pid=pid))
