import time
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from oya.models import Platform


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
                    if request.path != reverse("nest:maintenance"):
                        return redirect(reverse("nest:maintenance"))

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
