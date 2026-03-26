from toto.core.models import Platform
from django.http import Http404
from toto.core.serializers import PlatformSerializer
from django.conf import settings
from django.urls import reverse, NoReverseMatch


class PageProcessor:
    """
    Processes global context data for views.
    Injects platform configuration, theme, font, user info, and shared metadata.
    """

    def __init__(self, maintenance_mode=False):
        # If maintenance mode is active, skip platform lookup
        if maintenance_mode:
            self.config = None
        else:
            self.config = self._get_config()

    def _resolve_nav_items(self):
        items = []
        for item in getattr(settings, "HEADER_NAV_ITEMS", []):
            url_name = item.get("url_name")
            try:
                url = reverse(url_name)
            except NoReverseMatch:
                url = url_name  # fallback: literal URL

            items.append({
                "label": item["label"],
                "icon": item.get("icon", ""),
                "url": url,
            })
        return items

    def _get_config(self):
        platform = Platform.objects.filter(active=True).first()
        if not platform:
            raise Http404("No active platform configuration found.")
        return platform

    def decorate(self, context, request):
        # If maintenance mode, just inject minimal context
        if self.config is None:
            context.update({
                "platform": None,
                "user": request.user,
                "font": {},
                "theme": {},
                "is_authenticated": request.user.is_authenticated,
                "logo": None,
                "header_nav_items": self._resolve_nav_items()
            })
            return context

        # Normal mode: inject full platform data
        platform = PlatformSerializer(self.config).data
        theme_data = platform.get("theme") or {}

        context.update({
            "platform": platform,
            "user": request.user,
            "font": theme_data.get("font", {}),
            "theme": theme_data,
            "is_authenticated": request.user.is_authenticated,
            "logo": self.config.logo.url,
            "header_nav_items": self._resolve_nav_items()
        })
        return context
