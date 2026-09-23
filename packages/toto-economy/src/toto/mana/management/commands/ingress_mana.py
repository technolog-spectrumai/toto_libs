"""Price the mana-drawing metrics in their colours; report what is missing.

The pools themselves are not made here: ``toto.mana.bootstrap`` runs from
``IngressCommand.bootstrap()`` before EVERY ingress command, so by the time
this runs they exist (or this host cannot issue, which is reported).

**Prices are seeded once, then owned by staff.** A metric with no price row gets
the seed; a row that exists keeps its number — but its DENOMINATION is repaired
to the pool's asset, because a mana metric priced in ASR would drain a wallet
nobody can see. That repair is the one thing a deploy may overwrite.
"""

from __future__ import annotations

from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Price mana-drawing metrics in their pool's asset (seed once, repair denomination)."

    def process(self):
        from toto.mana import services

        pools = services.pools()
        if not pools:
            self.stdout.write(self.style.WARNING(
                "  ⚠ mana: no pools on this host (a branch, or no issuer yet) — "
                "nothing priced"))
            return
        for role, pool in sorted(pools.items()):
            self.stdout.write(
                f"  ✓ pool {role} = {pool.asset.unit_name} "
                f"(+{pool.regen_per_hour.normalize()}/h, max {pool.max_pool.normalize()})")

        undecided, absent = services.audit()
        if undecided:
            self.stdout.write(self.style.ERROR(
                "  ✗ mana: registered but neither coloured nor exempt — these run "
                f"FREE: {', '.join(undecided)} (toto/mana/colours.py)"))
        if absent:
            self.stdout.write(self.style.WARNING(
                f"  · mana: mapped but not metered on this host: {', '.join(absent)}"))

        seeded, repaired, kept = self._prices(services)
        filled = self._fill_existing(services)
        self.stdout.write(self.style.SUCCESS(
            f"✅  mana: {seeded} price(s) seeded, {repaired} re-denominated, "
            f"{kept} left as staff set them; {filled} member(s) filled"))

    def _fill_existing(self, services) -> int:
        """Members who joined before the pools existed start full, once.

        Keyed on the same ``signup`` claim a new member's fill uses, so a
        member is filled exactly once however they arrived — and every later
        deploy is a cheap no-op.
        """
        from django.contrib.auth import get_user_model

        filled = 0
        for user in get_user_model().objects.filter(is_active=True).iterator():
            if services.fill_pools(user):
                filled += 1
        return filled

    def _prices(self, services):
        from toto.quota.metrics import registry
        from toto.tariffs import rate_card
        from toto.tariffs.models import TariffItem

        seeded = repaired = kept = 0
        prices = services.seed_prices()
        for code in sorted(set(prices) | {c for c in services.colours.COLOUR_OF}):
            metric = registry.get(code)
            asset = services.asset_for(code)
            if metric is None or asset is None:
                continue
            item = (TariffItem.objects
                    .filter(tariff__code=rate_card.DEFAULT_TARIFF_CODE,
                            metric__code=code)
                    .select_related("charged_asset").first())
            if item is None:
                if code not in prices:
                    continue                    # mapped, deliberately unpriced
                rate_card.upsert_price(metric, prices[code], asset=asset)
                seeded += 1
                self.stdout.write(f"  + price {code} = {prices[code]} {asset.unit_name}")
            elif item.charged_asset_id != asset.pk:
                # A full save(): it re-derives the base units from the display
                # price and the NEW asset's decimals (rate_card's invariant).
                item.charged_asset = asset
                item.save()
                repaired += 1
                self.stdout.write(f"  ~ price {code} re-denominated in {asset.unit_name}")
            else:
                kept += 1
        return seeded, repaired, kept
