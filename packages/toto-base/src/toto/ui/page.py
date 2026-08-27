from django.conf import settings
from django.http import Http404
from django.urls import NoReverseMatch, reverse

from toto.core.models import Platform
from toto.core.serializers import PlatformSerializer


class PageProcessor:
    """
    Injects platform configuration, theme, font, user info, and nav items
    into the template context for every view.
    """

    def __init__(self, maintenance_mode=False):
        self.config = None if maintenance_mode else self._get_config()

    def _resolve_nav_items(self, request):
        items = []
        for item in getattr(settings, "HEADER_NAV_ITEMS", []):
            if item.get("requires_auth") and not request.user.is_authenticated:
                continue
            url_name = item.get("url_name")
            try:
                url = reverse(url_name)
            except NoReverseMatch:
                continue
            items.append({"label": item["label"], "icon": item.get("icon", ""), "url": url})
        return items

    def _get_config(self):
        platform = Platform.objects.filter(active=True).first()
        if not platform:
            raise Http404("No active platform configuration found.")
        return platform

    def decorate(self, context, request):
        base = {
            "user": request.user,
            "is_authenticated": request.user.is_authenticated,
            "header_nav_items": self._resolve_nav_items(request),
            "use_external_fonts": getattr(settings, "USE_EXTERNAL_FONTS", True),
        }

        if self.config is None:
            context.update({**base, "platform": None, "font": {}, "theme": {},
                            "logo": None, "federation": None})
            return context

        platform_data = PlatformSerializer(self.config).data
        theme_data = platform_data.get("theme") or {}
        context.update({
            **base,
            "platform": platform_data,
            "font": theme_data.get("font", {}),
            "theme": theme_data,
            "logo": self.config.logo.url if self.config.logo else None,
            # The platform's federation, for hosts that brand with it (the
            # holding identity). None-safe: a platform without one is the
            # common case, and stock templates ignore the key entirely.
            "federation": (
                {
                    "name": self.config.federation.name,
                    "description": self.config.federation.description,
                    "logo": (self.config.federation.logo.url
                             if self.config.federation.logo else None),
                }
                if self.config.federation_id else None
            ),
        })
        return context
