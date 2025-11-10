from oya.models import Platform
from django.http import Http404
from oya.serializers import PlatformSerializer


class PageProcessor:
    """
    Processes global context data for views.
    Injects platform configuration, theme, font, user info, and shared metadata.
    """

    def __init__(self):
        self.config = self._get_config()

    def _get_config(self):
        platform = Platform.objects.filter(active=True).first()
        if not platform:
            raise Http404("No active platform configuration found.")
        return platform

    def decorate(self, context, request):
        platform = PlatformSerializer(self.config).data
        federation_logo = None
        if hasattr(self.config, "federation") and self.config.federation.logo:
            federation_logo = self.config.federation.logo.url
        theme_data = {}
        if platform.get("theme"):
            theme_data = platform["theme"]
        context.update({
            "platform": platform,
            "user": request.user,
            "font": theme_data.get("font", {}),
            "theme": theme_data,
            "is_authenticated": request.user.is_authenticated,
            "federation_logo": federation_logo
        })
        return context
