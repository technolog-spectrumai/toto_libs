"""The recovery-ticket cards, rendered on the APPROVER's own profile.

Discovered by ``socialhub``'s ``autodiscover_plugins("plugins.profile_plugins")``
whenever this app is installed — no ready() hook of our own. The section sits
beside the reference-request cards and copies their shape on purpose: one
person vouching for another already has a visual language here.

``show_for_owner_only`` carries the access rule: the cards render only when the
profile's owner is the viewer, so a patron's queue is nobody else's business —
including the locked-out user, who could otherwise watch their own request's
state and learn who their approver is before that person has decided anything.
"""
from django.utils.translation import gettext_lazy as _

from toto.socialhub.plugins.profile_plugins import ProfilePlugin

from ..recovery import tickets_for_approver


@ProfilePlugin.plugin(
    key="recovery_tickets",
    title=_("Password Recovery Requests"),
    order=36,
)
class RecoveryTicketsProfilePlugin(ProfilePlugin):
    template_name = "sso/profile_plugins/recovery_tickets.html"
    section_icon = "fa-solid fa-user-shield"
    show_for_owner_only = True

    def _tickets(self, **kwargs):
        request = self.get_request_from_kwargs(**kwargs)
        if request is None or not request.user.is_authenticated:
            return None
        return tickets_for_approver(request.user)

    def is_visible_for_profile(self, **kwargs) -> bool:
        # An empty section would only make every profile ask "what is this?" —
        # render nothing until there is a card to act on.
        tickets = self._tickets(**kwargs)
        return tickets is not None and tickets.exists()

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        tickets = self._tickets(**kwargs)
        context["recovery_tickets"] = list(tickets) if tickets is not None else []
        return context
