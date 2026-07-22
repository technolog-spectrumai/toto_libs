def celery_available() -> bool:
    """True if at least one Celery worker responds within 1 s."""
    try:
        from celery import current_app
        return bool(current_app.control.ping(timeout=1.0))
    except Exception:
        return False
