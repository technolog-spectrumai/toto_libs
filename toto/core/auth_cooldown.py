import math
import time

from django.conf import settings


LOGIN_RETRY_COOLDOWN_SESSION_KEY = "login_retry_cooldown_until"


def login_retry_cooldown_seconds() -> int:
    return max(0, int(getattr(settings, "LOGIN_RETRY_COOLDOWN_SECONDS", 3)))


def login_retry_cooldown_remaining(request) -> int:
    until = request.session.get(LOGIN_RETRY_COOLDOWN_SESSION_KEY, 0)
    remaining = float(until) - time.time()
    return max(0, math.ceil(remaining))


def start_login_retry_cooldown(request) -> None:
    seconds = login_retry_cooldown_seconds()
    if seconds <= 0:
        request.session.pop(LOGIN_RETRY_COOLDOWN_SESSION_KEY, None)
        return
    request.session[LOGIN_RETRY_COOLDOWN_SESSION_KEY] = time.time() + seconds


def clear_login_retry_cooldown(request) -> None:
    request.session.pop(LOGIN_RETRY_COOLDOWN_SESSION_KEY, None)
