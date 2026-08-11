"""Test helpers for the ledger.

Every asset carries provenance, so every test that makes one needs an issuer.
Rather than edit a hundred ``setUp`` bodies, test modules swap their import::

    from toto.assets.testing import LedgerTestCase as TestCase

and every class in the module gets a monetary master for free.

Deliberately NOT a ``post_migrate`` receiver or a data migration: those run on
every host that migrates, which would manufacture a monetary authority on each
one. Minting is an operator act, and in tests it is a test act.
"""

from __future__ import annotations

from django.test import TestCase as DjangoTestCase
from django.test import TransactionTestCase as DjangoTransactionTestCase


def ensure_local_issuer(label: str = "Test master"):
    """The local monetary issuer, minted if this host has none yet."""
    from .issuer import local_issuer, mint_issuer

    return local_issuer() or mint_issuer(label=label)


class IssuerFixtureMixin:
    """Gives the test host a monetary issuer before anything else runs."""

    @classmethod
    def setUpTestData(cls):
        ensure_local_issuer()
        parent = super()
        if hasattr(parent, "setUpTestData"):
            parent.setUpTestData()


class LedgerTestCase(IssuerFixtureMixin, DjangoTestCase):
    pass


class LedgerTransactionTestCase(DjangoTransactionTestCase):
    """For suites that need real transactions; setUpTestData does not apply."""

    def setUp(self):
        ensure_local_issuer()
        super().setUp()


def make_asset(*, unit_name: str, name: str = "", decimals: int = 2,
               max_supply_base_units: int = 10 ** 12, **extra):
    """An asset with real provenance, for test fixtures.

    Tests used to build assets with ``Asset.objects.create``. Every asset now
    carries a genesis hash — enforced by a database constraint — so a fixture
    needs one too. This mints it the same way the issuance path does, rather
    than faking a hash, so the fixtures exercise the real shape.
    """
    from .currency_hash import build_genesis, compute_currency_hash
    from .models import Asset

    issuer = ensure_local_issuer()
    genesis = build_genesis(
        issuer_fingerprint=issuer.fingerprint, unit_name=unit_name,
        name=name or unit_name, decimals=decimals,
        max_supply_base_units=max_supply_base_units,
        issued_at="2026-01-01T00:00:00+00:00")
    return Asset.objects.create(
        name=name or unit_name, unit_name=unit_name, decimals=decimals,
        max_supply_base_units=max_supply_base_units,
        issuer=issuer, currency_hash=compute_currency_hash(genesis),
        genesis_payload=genesis, genesis_signature=issuer.sign_genesis(genesis),
        **extra)
