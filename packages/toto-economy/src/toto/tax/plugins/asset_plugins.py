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
        asset = self.get_asset_from_kwargs(**kwargs)
        if asset is None:
            return None
        from toto.tax.models import SurplusPolicy

        return (SurplusPolicy.objects
                .filter(asset=asset, active=True)
                .select_related("asset")
                .first())

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False
        return self._policy_for(**kwargs) is not None

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        policy = self._policy_for(**kwargs)
        from toto.tax.surplus import decimal_text, rate_pct_text

        context.update({
            "fee_policy": policy,
            "fee_threshold": decimal_text(policy.threshold_display),
            "fee_rate_pct": rate_pct_text(policy.rate),
        })
        request = kwargs.get("request")
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            from toto.tax.surplus import estimate_for_user

            for row in estimate_for_user(user):
                if row["unit"] == policy.asset.unit_name:
                    context["fee_mine"] = row
                    break
        return context
