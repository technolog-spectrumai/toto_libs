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

    def _resolve_nav_items(self, request):
        items = []
        for item in getattr(settings, "HEADER_NAV_ITEMS", []):
            # Skip items requiring auth if user is anonymous
            if item.get("requires_auth") and not request.user.is_authenticated:
                continue

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
        """
        Injects platform configuration, theme, user info, and navigation items
        into the template context.
        """

        # Base context shared in both modes
        base_context = {
            "user": request.user,
            "is_authenticated": request.user.is_authenticated,
            "header_nav_items": self._resolve_nav_items(request),
        }

        # Maintenance mode → minimal context
        if self.config is None:
            context.update({
                **base_context,
                "platform": None,
                "font": {},
                "theme": {},
                "logo": None,
            })
            return context

        # Normal mode → full platform data
        platform_data = PlatformSerializer(self.config).data
        theme_data = platform_data.get("theme") or {}

        context.update({
            **base_context,
            "platform": platform_data,
            "font": theme_data.get("font", {}),
            "theme": theme_data,
            "logo": self.config.logo.url if self.config.logo else None,
        })

        return context
