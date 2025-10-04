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
        context.update({
            "platform": platform,
            "font": platform["theme"].get("font", {}),
            "theme": platform["theme"],
            "user": request.user,
            "is_authenticated": request.user.is_authenticated
        })
        return context
