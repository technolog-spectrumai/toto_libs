from django.conf import settings
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


class ProfileTimezoneMiddleware:
    """For a signed-in member with a time zone on their Person, show every time
    in that zone for this request (2026-09-30); anyone else, and a member who
    left it blank, gets the platform's TIME_ZONE.

    Placed like ProfileLanguageMiddleware, after authentication, and it always
    deactivates afterwards: the activation is thread-local and a worker thread
    serves the next member too.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import zoneinfo

        from django.utils import timezone

        zone = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            try:
                name = user.community_profile.timezone
                if name:
                    zone = zoneinfo.ZoneInfo(name)
            except Exception:
                # No profile, or a name this Python no longer knows: the
                # default zone, never a 500 on every page.
                zone = None
        if zone is None:
            timezone.deactivate()
            return self.get_response(request)
        timezone.activate(zone)
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()


class PlatformMiddleware:
    """Redirect to the maintenance page while Platform.active is False.

    That is all it does. It ran a per-IP rate limiter too, until it turned out
    the refusal had been commented out and only the cache round-trips remained.
    Rate limiting is nginx's job now.

    This runs early on every request, so keep it cheap and keep everything it
    touches bounded.
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

            # There used to be a rate limiter here. Its refusal was commented
            # out, so the cache.get/cache.set pair around it ran on every single
            # request and fed a decision that was never taken — two Redis
            # round-trips per request, in the second middleware, for nothing.
            #
            # Worse than useless: with no socket timeout on the cache client, a
            # *hung* Redis (a BGSAVE stall, swap, a dropped conntrack entry)
            # leaves the connection established and recv() blocking forever.
            # That is not an exception, so the guard below never caught it — the
            # whole host wedged in its second middleware, healthcheck included.
            #
            # Rate limiting belongs in nginx, where it is now (limit_req on
            # /sso/), and where it costs the app nothing.

        except Exception:
            # Fail gracefully if the DB is not ready. Note this only covers
            # errors, never hangs — anything reached from here must carry its
            # own timeout.
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
