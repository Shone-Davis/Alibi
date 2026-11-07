from app import db
from app.models import Setting


def get_settings():
    """The seller's settings, created with defaults on first use."""
    settings = Setting.query.first()
    if settings is None:
        settings = Setting(refund_threshold=15, urgent_hours=48)
        db.session.add(settings)
        db.session.commit()
    return settings
