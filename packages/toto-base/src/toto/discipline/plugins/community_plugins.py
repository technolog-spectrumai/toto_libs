"""The community directory's Aura column: which aura each listed community
has and how fast it regenerates, for everybody who reads the directory."""

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from toto.socialhub.plugins.community_plugins import CommunityListPlugin


@CommunityListPlugin.plugin(key="discipline_aura", title=gettext_lazy("Aura"), order=20)
class AuraColumn(CommunityListPlugin):
    def cells(self, communities, **kwargs) -> dict:
        from ..models import CommunityAura
        from ..services import _amount

        links = (CommunityAura.objects.filter(community__in=communities)
                 .select_related("aura"))
        return {link.community_id: _("%(aura)s · %(rate)s an hour") % {
                    "aura": link.aura.name, "rate": _amount(link.aura.regen_per_hour)}
                for link in links}
