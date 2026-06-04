"""
LedgerBackend — thin abstraction over the asset ledger engine.

The default backend delegates to the native Django/PostgreSQL implementation.
To swap in a different engine (e.g., Algorand), subclass LedgerBackend and
override the three core methods, then point ASSETS_BACKEND in settings to
your class.

Example settings.py:
    ASSETS_BACKEND = "myapp.backends.AlgorandLedgerBackend"

All service code should call `get_backend()` rather than importing the
native service functions directly.
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils.module_loading import import_string

if TYPE_CHECKING:
    from .models import Asset, LedgerAccount, LedgerTransaction


class LedgerBackend:
    """
    Abstract interface for the asset ledger engine.

    Backends must be stateless — do not store per-request state on self.
    """

    def create_asset(
        self,
        *,
        name: str,
        unit_name: str,
        total_supply: Decimal,
        decimals: int,
        reserve_account: "LedgerAccount",
        reference: str,
        description: str = "",
        metadata=None,
    ) -> "Asset":
        raise NotImplementedError

    def transfer_asset(
        self,
        *,
        asset: "Asset",
        sender_account: "LedgerAccount",
        receiver_account: "LedgerAccount",
        amount: Decimal,
        reference: str,
        description: str = "",
        metadata=None,
    ) -> "LedgerTransaction":
        raise NotImplementedError

    def reverse_transaction(
        self,
        *,
        transaction: "LedgerTransaction",
        reference: str,
        description: str = "",
        metadata=None,
    ) -> "LedgerTransaction":
        raise NotImplementedError


class DjangoLedgerBackend(LedgerBackend):
    """
    Native Django/PostgreSQL ledger backend.
    Delegates to toto.assets.services.assets.
    """

    def create_asset(self, **kwargs) -> "Asset":
        from .services.assets import create_asset
        return create_asset(**kwargs)

    def transfer_asset(self, **kwargs) -> "LedgerTransaction":
        from .services.assets import transfer_asset
        return transfer_asset(**kwargs)

    def reverse_transaction(self, **kwargs) -> "LedgerTransaction":
        from .services.assets import reverse_transaction
        return reverse_transaction(**kwargs)


def get_backend() -> LedgerBackend:
    path = getattr(settings, "ASSETS_BACKEND", "toto.assets.backend.DjangoLedgerBackend")
    cls = import_string(path)
    return cls()
