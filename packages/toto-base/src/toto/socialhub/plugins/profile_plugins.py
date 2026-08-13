from typing import Any, ClassVar

from toto.core.plugin import BasePlugin

#: The metric the head tax is levied on, declared by socialhub/taxes.py.
HEAD_TAX_METRIC = "civics.head"


class ProfilePlugin(BasePlugin):
    """
    Base class for plugins rendered on Person profile pages.
    """

    registry: ClassVar[dict[str, "ProfilePlugin"]] = {}

    section_icon: ClassVar[str] = "fa-solid fa-puzzle-piece"
    show_for_owner_only: ClassVar[bool] = False

    @staticmethod
    def get_profile_from_kwargs(**kwargs):
        return kwargs.get("profile")

    @staticmethod
    def get_request_from_kwargs(**kwargs):
        return kwargs.get("request")

    def is_profile_owner(self, **kwargs) -> bool:
        request = self.get_request_from_kwargs(**kwargs)
        profile = self.get_profile_from_kwargs(**kwargs)

        return bool(
            request
            and request.user.is_authenticated
            and profile
            and profile.user == request.user
        )

    def is_visible_for_profile(self, **kwargs) -> bool:
        return True

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False

        profile = self.get_profile_from_kwargs(**kwargs)

        if profile is None:
            return False

        if self.show_for_owner_only and not self.is_profile_owner(**kwargs):
            return False

        return self.is_visible_for_profile(**kwargs)

    def get_context(self, **kwargs) -> dict[str, Any]:
        context = super().get_context(**kwargs)

        context.update(
            {
                "profile_plugin": self,
                "profile_plugin_key": self.get_key(),
                "profile_plugin_title": self.get_title(),
                "profile_plugin_icon": self.section_icon,
            }
        )

        return context


@ProfilePlugin.plugin(key="head_tax", title="Head tax", order=30)
class HeadTaxProfilePlugin(ProfilePlugin):
    """What this person's communities cost them in head tax.

    Announced here rather than inside the Communities grid because a plugin can
    only contribute a whole section — the registry appends, it does not inject
    — and because the number is about the union of the communities, not about
    any one card.

    Two things this wording has to get right, and both are easy to get wrong:

    * **A community never pays.** The levy samples users
      (:mod:`toto.socialhub.taxes`), and PRIVILEGES.md is explicit that a
      community receives nothing and owes nothing. The line reads "members of
      this community pay", never "this community pays".
    * **The lowest weight wins.** Belonging to a second, better-standing
      community can only help, so the effective figure is a minimum and the
      per-community figures are shown beside it to make that legible.

    The rest of :class:`~toto.socialhub.models.CommunityPrivilege` stays
    admin-only. The head weight is the one field that is announced, because it
    is the one that decides what somebody owes.
    """

    template_name = "socialhub/profile_plugins/head_tax.html"
    section_icon = "fa-solid fa-scale-balanced"

    def is_visible_for_profile(self, **kwargs) -> bool:
        """Only where a head tax is a thing that exists.

        The provider registers when :mod:`toto.socialhub.taxes` is imported,
        and that import happens only through ``toto.tax``'s autodiscovery — so
        on a host with no levy engine this answers False without naming
        ``toto.tax`` anywhere.
        """
        from toto.quota import levies

        profile = self.get_profile_from_kwargs(**kwargs)
        if profile is None or levies.of(HEAD_TAX_METRIC) is None:
            return False
        return profile.communities.exists()

    def get_context(self, **kwargs):
        from toto.quota import levies

        from toto.socialhub.privileges import ORDINARY_HEAD_WEIGHT, head_weight_for

        context = super().get_context(**kwargs)
        profile = self.get_profile_from_kwargs(**kwargs)

        rows = []
        for community in profile.communities.all():
            privilege = getattr(community, "privilege", None)
            weight = ORDINARY_HEAD_WEIGHT if privilege is None else privilege.head_weight
            rows.append({"community": community, "weight": weight})
        rows.sort(key=lambda row: (row["weight"], row["community"].name))

        levy = levies.of(HEAD_TAX_METRIC) or {}
        context.update({
            "head_tax_rows": rows,
            "head_tax_ordinary": ORDINARY_HEAD_WEIGHT,
            # The person's effective rate, from the one resolver the nightly
            # sweep also asks — so the page and the charge cannot disagree.
            "head_tax_effective": head_weight_for(getattr(profile, "user", None)),
            # None means "no engine to bill it", which is not the same
            # statement as "armed and switched off" — levies.of draws that line
            # and the template says which it is.
            "head_tax_armed": levy.get("active"),
        })
        return context
