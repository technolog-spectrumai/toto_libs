from typing import Any, ClassVar

from toto.core.plugin import BasePlugin
from toto.socialhub.models import CommunityNewsPost
from toto.socialhub.permissions import can_manage_community_news


class CommunityPlugin(BasePlugin):
    """
    Base class for plugins rendered on SocialHub community detail pages.
    """

    registry: ClassVar[dict[str, "CommunityPlugin"]] = {}

    section_icon: ClassVar[str] = "fa-solid fa-puzzle-piece"

    @staticmethod
    def get_community_from_kwargs(**kwargs):
        return kwargs.get("community")

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False
        return self.get_community_from_kwargs(**kwargs) is not None

    def get_context(self, **kwargs) -> dict[str, Any]:
        context = super().get_context(**kwargs)
        context.update({
            "community_plugin": self,
            "community_plugin_key": self.get_key(),
            "community_plugin_title": self.get_title(),
            "community_plugin_icon": self.section_icon,
        })
        return context


@CommunityPlugin.plugin(key="community_news", title="Community News", order=20)
class CommunityNewsPlugin(CommunityPlugin):
    section_icon = "fa-solid fa-bullhorn"
    template_name = "socialhub/community_plugins/news.html"

    def get_context(self, **kwargs) -> dict[str, Any]:
        context = super().get_context(**kwargs)
        community = kwargs["community"]
        request = kwargs.get("request")
        posts = (
            CommunityNewsPost.objects
            .filter(community=community)
            .select_related("author", "community")
            .prefetch_related("topics")[:8]
        )
        context.update({
            "community_news": posts,
            "can_manage_news": can_manage_community_news(request, community) if request else False,
        })
        return context
