from oya.models import Platform
from django.http import Http404
from oya.serializers import PlatformSerializer


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
                "federation_logo": None,
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
            "federation_logo": self.config.logo.url
        })
        return context
