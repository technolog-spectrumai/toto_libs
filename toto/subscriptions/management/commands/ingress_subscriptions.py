from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from toto.assets.models import Asset, LedgerAccount, to_base_units
from toto.socialhub.models import Community

from ...models import SubscriptionFeature, SubscriptionPlan, SubscriptionPrice


class Command(BaseCommand):
    help = "Seed example subscription plans for AI, storage, and academy bundles."

    def add_arguments(self, parser):
        parser.add_argument("--community", default="default", help="Community slug or name")
        parser.add_argument("--asset", default="ASSARI", help="Asset unit_name/ticker for pricing")
        parser.add_argument("--receiver", default="subscription-revenue", help="Receiving LedgerAccount code")

    def handle(self, *args, **options):
        community_key = options["community"]
        community = Community.objects.filter(slug=community_key).first() or Community.objects.filter(name=community_key).first()
        if not community:
            self.stderr.write(self.style.ERROR(f"Community not found: {community_key}"))
            return

        asset = Asset.objects.filter(unit_name=options["asset"]).first() or Asset.objects.filter(name=options["asset"]).first()
        if not asset:
            self.stderr.write(self.style.ERROR(f"Asset not found: {options['asset']}"))
            return

        receiver, _ = LedgerAccount.objects.get_or_create(
            code=options["receiver"],
            defaults={"name": "Subscription Revenue", "account_type": "revenue", "active": True},
        )

        specs = [
            {
                "name": "AI Starter",
                "code": "ai-starter",
                "kind": SubscriptionPlan.PlanKind.AI,
                "amount": Decimal("25"),
                "features": [("ai.input_tokens", "Input tokens", "allowance", Decimal("1000000"), "tokens", True)],
            },
            {
                "name": "Storage 100GB",
                "code": "storage-100gb",
                "kind": SubscriptionPlan.PlanKind.STORAGE,
                "amount": Decimal("10"),
                "features": [("storage.bytes", "Storage capacity", "allowance", Decimal("100"), "GB", True)],
            },
            {
                "name": "Academy Pass",
                "code": "academy-pass",
                "kind": SubscriptionPlan.PlanKind.ACADEMY,
                "amount": Decimal("40"),
                "features": [("academy.lessons", "Included lessons", "allowance", Decimal("30"), "lessons", False)],
            },
        ]

        for spec in specs:
            plan, _ = SubscriptionPlan.objects.update_or_create(
                community=community,
                code=spec["code"],
                defaults={
                    "name": spec["name"],
                    "slug": slugify(spec["name"]),
                    "kind": spec["kind"],
                    "status": SubscriptionPlan.Status.ACTIVE,
                    "description": f"Seeded {spec['name']} subscription plan.",
                    "is_public": True,
                    "metadata": {"seeded_by": "ingress_subscriptions"},
                },
            )
            SubscriptionPrice.objects.update_or_create(
                plan=plan,
                code="monthly",
                defaults={
                    "name": "Monthly",
                    "asset": asset,
                    "amount_base_units": to_base_units(spec["amount"], asset.decimals),
                    "interval": "monthly",
                    "interval_count": 1,
                    "receiving_account": receiver,
                    "is_active": True,
                },
            )
            for code, name, kind, qty, unit, hard in spec["features"]:
                SubscriptionFeature.objects.update_or_create(
                    plan=plan,
                    code=code,
                    defaults={
                        "name": name,
                        "kind": kind,
                        "quantity": qty,
                        "unit": unit,
                        "hard_limit": hard,
                        "active": True,
                    },
                )
            self.stdout.write(self.style.SUCCESS(f"Seeded plan {plan.code}"))

        self.stdout.write(self.style.SUCCESS("Subscription ingress complete."))
