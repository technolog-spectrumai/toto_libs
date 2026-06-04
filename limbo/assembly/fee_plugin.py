"""
Registers the Assembly transaction fee plugin into the Assets FeePluginRegistry.
Imported by AssemblyConfig.ready() — assets never imports this module.
"""
from toto.assets.fee_plugins import FeePluginBase, FeePluginRegistry


class AssemblyFeePlugin(FeePluginBase):
    """
    Effective fee = blanket fee (asset=None) + per-asset adjustment, floored at 0.
    """

    def get_fee_bps(self, asset, *, community=None, account=None, **kwargs) -> int:
        if community is None:
            return 0
        try:
            from .models import CommunityTransactionFee
            blanket = CommunityTransactionFee.objects.filter(
                community=community, asset__isnull=True, active=True
            ).order_by("-created_at").first()
            specific = CommunityTransactionFee.objects.filter(
                community=community, asset=asset, active=True
            ).order_by("-created_at").first()
            total = (blanket.fee_bps if blanket else 0) + (specific.fee_bps if specific else 0)
            return max(0, total)
        except Exception:
            pass
        return 0


FeePluginRegistry.register(AssemblyFeePlugin())
