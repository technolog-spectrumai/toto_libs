from toto.socialhub.models import Community
from .community_session import get_community_slug


def capitol_community(request):
    if not request.user.is_authenticated:
        return {}
    communities = list(Community.objects.select_related("assembly_config").order_by("name"))
    if not communities:
        return {"communities": [], "selected_community": None}
    slug = get_community_slug(request.user.pk)
    selected = next((c for c in communities if c.slug == slug), None) or communities[0]
    return {"communities": communities, "selected_community": selected}
