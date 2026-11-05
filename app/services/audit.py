from app import db
from app.models import AuditLog


def record(dispute, actor, event, detail=None):
    """Add an audit entry to the current transaction; the caller commits."""
    db.session.add(AuditLog(dispute=dispute, actor=actor, event=event, detail=detail or {}))
