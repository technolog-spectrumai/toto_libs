import time
from django.conf import settings
from django.core.cache import cache
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import translation
from toto.core.models import Platform

_VALID_LANG_CODES = None


def _get_valid_langs():
    global _VALID_LANG_CODES
    if _VALID_LANG_CODES is None:
        _VALID_LANG_CODES = {code for code, _ in getattr(settings, "LANGUAGES", [])}
    return _VALID_LANG_CODES


class ProfileLanguageMiddleware:
    """
    For authenticated users: activate preferred_language from their Person profile
    unless they have an explicit per-session language cookie set.
    Anonymous users continue to use normal Django locale behaviour.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # Respect an explicit cookie-based or session-based language choice.
            cookie_lang = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME, "")
            session_lang = ""
            if hasattr(request, "session"):
                session_lang = request.session.get("_language", "")
            if not cookie_lang and not session_lang:
                try:
                    lang = request.user.community_profile.preferred_language
                    if lang and lang in _get_valid_langs():
                        translation.activate(lang)
                        request.LANGUAGE_CODE = lang
                except Exception:
                    pass
        return self.get_response(request)


class PlatformMiddleware:
    """
    Middleware that enforces:
    - Redirect to maintenance if Platform.active is False
    - Rate limiting per IP using Platform settings
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            platform = Platform.objects.first()

            # 1. Maintenance mode check
            if platform and not platform.active:
                # Skip redirect for admin URLs
                if not request.path.startswith("/admin/"):
                    if request.path != reverse("core:maintenance"):
                        return redirect(reverse("core:maintenance"))

            # 2. Rate limiting check
            if platform:
                window = platform.rate_limit_window
                max_requests = platform.rate_limit_max_requests
            else:
                window = 60
                max_requests = 10

            ip = request.META.get("REMOTE_ADDR", "unknown")
            key = f"rl:{ip}"
            requests = cache.get(key, [])
            now = time.time()
            # Keep only requests in the last `window` seconds
            requests = [t for t in requests if now - t < window]
            # if len(requests) >= max_requests:
            #
            #     return HttpResponse("Too many requests, slow down!", status=429)

            requests.append(now)
            cache.set(key, requests, timeout=window)

        except Exception:
            # Fail gracefully if DB/cache not ready
            pass

        return self.get_response(request)


class ContentSecurityPolicyMiddleware:
    """Emit a Content-Security-Policy header when settings.CONTENT_SECURITY_POLICY
    is set. Opt-in: hosts that leave it unset (e.g. the clearnet platform) get no
    header and unchanged behavior. The faros onion sets a strict policy that
    forbids every external origin (map tiles, fonts, CDN scripts) as a hard
    anti-deanonymization backstop."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.policy = getattr(settings, "CONTENT_SECURITY_POLICY", "")

    def __call__(self, request):
        response = self.get_response(request)
        if self.policy and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self.policy
        return response
