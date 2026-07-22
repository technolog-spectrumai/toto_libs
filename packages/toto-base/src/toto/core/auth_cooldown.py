import math
import time

from django.conf import settings


LOGIN_RETRY_COOLDOWN_SESSION_KEY = "login_retry_cooldown_until"
CAPTCHA_RETRY_COOLDOWN_SESSION_KEY = "captcha_retry_cooldown_until"


def _cooldown_seconds(setting_name: str, default: int) -> int:
    return max(0, int(getattr(settings, setting_name, default)))


def _cooldown_remaining(request, session_key: str) -> int:
    until = request.session.get(session_key, 0)
    remaining = float(until) - time.time()
    return max(0, math.ceil(remaining))


def _start_cooldown(request, session_key: str, seconds: int) -> None:
    if seconds <= 0:
        request.session.pop(session_key, None)
        return
    request.session[session_key] = time.time() + seconds


def _clear_cooldown(request, session_key: str) -> None:
    request.session.pop(session_key, None)


# --- login retry cooldown ---------------------------------------------------

def login_retry_cooldown_seconds() -> int:
    return _cooldown_seconds("LOGIN_RETRY_COOLDOWN_SECONDS", 3)


def login_retry_cooldown_remaining(request) -> int:
    return _cooldown_remaining(request, LOGIN_RETRY_COOLDOWN_SESSION_KEY)


def start_login_retry_cooldown(request) -> None:
    _start_cooldown(request, LOGIN_RETRY_COOLDOWN_SESSION_KEY, login_retry_cooldown_seconds())


def clear_login_retry_cooldown(request) -> None:
    _clear_cooldown(request, LOGIN_RETRY_COOLDOWN_SESSION_KEY)


# --- captcha retry cooldown (registration / membership verification) --------

def captcha_retry_cooldown_seconds() -> int:
    return _cooldown_seconds("CAPTCHA_RETRY_COOLDOWN_SECONDS", 3)


def captcha_retry_cooldown_remaining(request) -> int:
    return _cooldown_remaining(request, CAPTCHA_RETRY_COOLDOWN_SESSION_KEY)


def start_captcha_retry_cooldown(request) -> None:
    _start_cooldown(request, CAPTCHA_RETRY_COOLDOWN_SESSION_KEY, captcha_retry_cooldown_seconds())


def clear_captcha_retry_cooldown(request) -> None:
    _clear_cooldown(request, CAPTCHA_RETRY_COOLDOWN_SESSION_KEY)
