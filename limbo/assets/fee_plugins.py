"""
Fee plugin registry — decoupled hook for apps to add transaction fees to assets.

Usage (from any app's apps.py ready()):
    from toto.assets.fee_plugins import FeePluginRegistry, FeePluginBase
    FeePluginRegistry.register(MyFeePlugin())
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from toto.assets.models import Asset


class FeePluginBase:
    """
    Override get_fee_bps to return additional basis-point fee for a given asset+context.
    Return 0 if this plugin does not apply.
    """
    def get_fee_bps(self, asset: "Asset", *, community=None, account=None, **kwargs) -> int:
        return 0


class _FeePluginRegistry:
    def __init__(self):
        self._plugins: list[FeePluginBase] = []

    def register(self, plugin: FeePluginBase) -> None:
        self._plugins.append(plugin)

    def get_total_fee_bps(self, asset: "Asset", *, community=None, account=None, **kwargs) -> int:
        return sum(
            p.get_fee_bps(asset, community=community, account=account, **kwargs)
            for p in self._plugins
        )

    def all(self) -> list[FeePluginBase]:
        return list(self._plugins)


FeePluginRegistry = _FeePluginRegistry()
