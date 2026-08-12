"""The community fee's section on the asset detail page.

Discovered by assets' ready() (autodiscover_plugins("plugins.asset_plugins")),
so the fee renders where the asset is described with zero edits to assets.
Model imports stay inside methods — the WalletProfilePlugin discipline.
"""

from toto.assets.plugins.asset_plugins import AssetPlugin


@AssetPlugin.plugin(key="community_fee", title="Community fee", order=40)
class CommunityFeeAssetPlugin(AssetPlugin):
    template_name = "tax/plugins/asset_community_fee.html"
    section_icon = "fa-solid fa-hand-holding-dollar"

    def _policy_for(self, **kwargs):
        """The asset's policy, active or not.

        Inactive rows are returned too, which they were not before: staff edit
        this section now, and a paused fee that vanishes from the page is a fee
        nobody can un-pause. Visibility for a MEMBER still turns on `active`.
        """
        asset = self.get_asset_from_kwargs(**kwargs)
        if asset is None:
            return None
        from toto.tax.models import SurplusPolicy

        return (SurplusPolicy.objects
                .filter(asset=asset)
                .select_related("asset")
                .first())

    def _can_edit(self, **kwargs) -> bool:
        request = kwargs.get("request")
        user = getattr(request, "user", None)
        return bool(user is not None and getattr(user, "is_staff", False))

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False
        # Staff always see the section, so a fee can be CREATED where none
        # exists. It used to render only when a policy was already there, which
        # made the one screen that could create one the old allowances desk —
        # and that desk is gone, because it configured two unrelated objects.
        if self._can_edit(**kwargs):
            return True
        policy = self._policy_for(**kwargs)
        return policy is not None and policy.active

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        asset = self.get_asset_from_kwargs(**kwargs)
        policy = self._policy_for(**kwargs)
        from toto.tax.models import SurplusPeriod
        from toto.tax.surplus import decimal_text, rate_pct_text

        context.update({
            "fee_asset": asset,
            "fee_policy": policy,
            "fee_active": bool(policy and policy.active),
            "fee_threshold": decimal_text(policy.threshold_display) if policy else "",
            "fee_rate_pct": rate_pct_text(policy.rate) if policy else "",
            "fee_can_edit": self._can_edit(**kwargs),
            "fee_periods": SurplusPeriod.choices,
            "fee_period": policy.period if policy else SurplusPeriod.values[0],
        })
        request = kwargs.get("request")
        user = getattr(request, "user", None)
        if policy is not None and user is not None and getattr(user, "is_authenticated", False):
            from toto.tax.surplus import estimate_for_user

            for row in estimate_for_user(user):
                if row["unit"] == policy.asset.unit_name:
                    context["fee_mine"] = row
                    break
        return context
