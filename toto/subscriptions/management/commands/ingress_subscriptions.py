from __future__ import annotations

from decimal import Decimal

from django.utils import timezone
from django.utils.text import slugify

from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed demo subscription plans, customers, and subscriptions for user testing."

    def process(self):
        if not self.full:
            return
        self.stdout.write("📦  Seeding demo subscriptions…")

        accounts = self._seed_accounts()
        asset = self._seed_asset(accounts)
        community = self._seed_community()
        plans = self._seed_plans(community, asset, accounts)

        if self.full:
            self._seed_customers_and_subscriptions(community, asset, plans, accounts)

        self.stdout.write(self.style.SUCCESS("✅  Subscriptions ingress complete."))

    # ------------------------------------------------------------------ #
    # Accounts                                                             #
    # ------------------------------------------------------------------ #

    def _seed_accounts(self) -> dict:
        from toto.assets.models import LedgerAccount

        specs = [
            ("sub-reserve",        "Subscriptions Reserve",        "reserve"),
            ("sub-revenue",        "Subscriptions Revenue",        "revenue"),
            ("sub-billing-alice",  "Alice – Billing",              "user"),
            ("sub-billing-bob",    "Bob – Billing",                "user"),
            ("sub-billing-carol",  "Carol – Billing",              "user"),
            ("sub-billing-dave",   "Dave – Billing",               "user"),
        ]
        accounts = {}
        for code, name, atype in specs:
            acc, created = LedgerAccount.objects.get_or_create(
                code=code,
                defaults={"name": name, "account_type": atype, "active": True},
            )
            accounts[code] = acc
            if created:
                self.stdout.write(f"  + account {code}")
        return accounts

    # ------------------------------------------------------------------ #
    # Asset                                                                #
    # ------------------------------------------------------------------ #

    def _seed_asset(self, accounts: dict):
        from toto.assets.models import Asset, LedgerTransaction
        from toto.assets.services.assets import create_asset

        ref = "create-sdemo"
        if LedgerTransaction.objects.filter(reference=ref).exists():
            asset = Asset.objects.get(unit_name="SDEMO")
            self.stdout.write(self.style.WARNING("  ⚠ skipped existing asset SDEMO"))
            return asset

        asset = create_asset(
            name="Subscriptions Demo Token",
            unit_name="SDEMO",
            total_supply=Decimal("1000000"),
            decimals=2,
            reserve_account=accounts["sub-reserve"],
            reference=ref,
            description="Demo token used exclusively by the subscriptions ingress.",
        )
        self.stdout.write("  + asset SDEMO")
        return asset

    # ------------------------------------------------------------------ #
    # Community                                                            #
    # ------------------------------------------------------------------ #

    def _seed_community(self):
        from toto.socialhub.models import Community

        community, created = Community.objects.get_or_create(
            slug="demo-subscriptions",
            defaults={"name": "Demo Subscriptions", "org_type": Community.COMPANY},
        )
        if created:
            self.stdout.write("  + community demo-subscriptions")
        return community

    # ------------------------------------------------------------------ #
    # Plans                                                                #
    # ------------------------------------------------------------------ #

    def _seed_plans(self, community, asset, accounts: dict) -> dict:
        from toto.subscriptions.models import SubscriptionFeature, SubscriptionPlan, SubscriptionPrice
        from toto.assets.models import to_base_units

        receiver = accounts["sub-revenue"]

        specs = [
            {
                "name": "AI Starter",
                "code": "ai-starter",
                "kind": SubscriptionPlan.PlanKind.AI,
                "description": "Entry-level AI plan — 1M input tokens per month.",
                "amount_monthly": Decimal("25.00"),
                "trial_days": 14,
                "features": [
                    ("ai.input_tokens",  "Input tokens",   "allowance",    Decimal("1000000"), "tokens",  True),
                    ("ai.output_tokens", "Output tokens",  "allowance",    Decimal("200000"),  "tokens",  True),
                    ("ai.api_access",    "API access",     "entitlement",  None,               "",        False),
                ],
            },
            {
                "name": "AI Pro",
                "code": "ai-pro",
                "kind": SubscriptionPlan.PlanKind.AI,
                "description": "Professional AI plan — 10M tokens, fine-tuning, priority support.",
                "amount_monthly": Decimal("99.00"),
                "trial_days": 7,
                "features": [
                    ("ai.input_tokens",     "Input tokens",     "allowance",    Decimal("10000000"), "tokens",  True),
                    ("ai.output_tokens",    "Output tokens",    "allowance",    Decimal("2000000"),  "tokens",  True),
                    ("ai.api_access",       "API access",       "entitlement",  None,                "",        False),
                    ("ai.fine_tuning",      "Fine-tuning",      "entitlement",  None,                "",        False),
                    ("ai.priority_support", "Priority support", "service_level",None,                "",        False),
                ],
            },
            {
                "name": "Storage 100GB",
                "code": "storage-100gb",
                "kind": SubscriptionPlan.PlanKind.STORAGE,
                "description": "100 GB object storage with CDN.",
                "amount_monthly": Decimal("10.00"),
                "trial_days": 0,
                "features": [
                    ("storage.bytes",     "Storage capacity", "allowance",   Decimal("100"),  "GB", True),
                    ("storage.bandwidth", "Bandwidth",        "allowance",   Decimal("1000"), "GB", True),
                    ("storage.cdn",       "CDN access",       "entitlement", None,            "",   False),
                ],
            },
            {
                "name": "Storage 1TB",
                "code": "storage-1tb",
                "kind": SubscriptionPlan.PlanKind.STORAGE,
                "description": "1 TB storage with CDN and custom domain.",
                "amount_monthly": Decimal("40.00"),
                "trial_days": 0,
                "features": [
                    ("storage.bytes",         "Storage capacity", "allowance",   Decimal("1024"),  "GB", True),
                    ("storage.bandwidth",     "Bandwidth",        "allowance",   Decimal("10000"), "GB", True),
                    ("storage.cdn",           "CDN access",       "entitlement", None,             "",   False),
                    ("storage.custom_domain", "Custom domain",    "entitlement", None,             "",   False),
                ],
            },
            {
                "name": "Academy Pass",
                "code": "academy-pass",
                "kind": SubscriptionPlan.PlanKind.ACADEMY,
                "description": "30 curated lessons per month with live event access.",
                "amount_monthly": Decimal("40.00"),
                "trial_days": 7,
                "features": [
                    ("academy.lessons",     "Included lessons",  "allowance",   Decimal("30"),  "lessons", False),
                    ("academy.live_events", "Live event access", "entitlement", None,           "",        False),
                    ("academy.downloads",   "Lesson downloads",  "allowance",   Decimal("50"),  "files",   False),
                ],
            },
            {
                "name": "Academy Pro",
                "code": "academy-pro",
                "kind": SubscriptionPlan.PlanKind.ACADEMY,
                "description": "200 lessons, downloads, and 1-on-1 coaching sessions.",
                "amount_monthly": Decimal("120.00"),
                "trial_days": 7,
                "features": [
                    ("academy.lessons",     "Included lessons",  "allowance",   Decimal("200"), "lessons",  False),
                    ("academy.live_events", "Live event access", "entitlement", None,           "",         False),
                    ("academy.downloads",   "Lesson downloads",  "allowance",   Decimal("500"), "files",    False),
                    ("academy.coaching",    "1-on-1 coaching",   "allowance",   Decimal("2"),   "sessions", False),
                ],
            },
            {
                "name": "Community Basic",
                "code": "community-basic",
                "kind": SubscriptionPlan.PlanKind.COMMUNITY,
                "description": "For small communities — up to 100 members, 5 channels.",
                "amount_monthly": Decimal("15.00"),
                "trial_days": 30,
                "features": [
                    ("community.seats",    "Member seats",  "allowance", Decimal("100"), "seats",    True),
                    ("community.posts",    "Posts/month",   "allowance", Decimal("500"), "posts",    False),
                    ("community.channels", "Channels",      "allowance", Decimal("5"),   "channels", True),
                ],
            },
            {
                "name": "Support Pro",
                "code": "support-pro",
                "kind": SubscriptionPlan.PlanKind.SUPPORT,
                "description": "Priority support — 20 tickets/month, 4-hour SLA, onboarding call.",
                "amount_monthly": Decimal("200.00"),
                "trial_days": 0,
                "features": [
                    ("support.tickets",   "Tickets/month", "allowance",    Decimal("20"), "tickets", False),
                    ("support.sla_4h",    "4-hour SLA",    "service_level",None,          "",        False),
                    ("support.onboarding","Onboarding call","entitlement",  None,          "",        False),
                ],
            },
        ]

        plans = {}
        for spec in specs:
            plan, _ = SubscriptionPlan.objects.update_or_create(
                community=community,
                code=spec["code"],
                defaults={
                    "name": spec["name"],
                    "slug": slugify(spec["name"]),
                    "kind": spec["kind"],
                    "status": SubscriptionPlan.Status.ACTIVE,
                    "description": spec["description"],
                    "is_public": True,
                    "trial_days": spec["trial_days"],
                    "metadata": {"seeded_by": "ingress_subscriptions"},
                },
            )
            monthly_units = to_base_units(spec["amount_monthly"], asset.decimals)
            SubscriptionPrice.objects.update_or_create(
                plan=plan, code="monthly",
                defaults={
                    "name": "Monthly",
                    "asset": asset,
                    "amount_base_units": monthly_units,
                    "interval": "monthly",
                    "interval_count": 1,
                    "receiving_account": receiver,
                    "is_active": True,
                },
            )
            yearly_units = to_base_units(spec["amount_monthly"] * 10, asset.decimals)
            SubscriptionPrice.objects.update_or_create(
                plan=plan, code="yearly",
                defaults={
                    "name": "Yearly",
                    "asset": asset,
                    "amount_base_units": yearly_units,
                    "interval": "yearly",
                    "interval_count": 1,
                    "receiving_account": receiver,
                    "is_active": True,
                },
            )
            for code, name, kind, qty, unit, hard in spec["features"]:
                SubscriptionFeature.objects.update_or_create(
                    plan=plan, code=code,
                    defaults={
                        "name": name,
                        "kind": kind,
                        "quantity": qty,
                        "unit": unit,
                        "hard_limit": hard,
                        "active": True,
                    },
                )
            plans[spec["code"]] = plan
            self.stdout.write(self.style.SUCCESS(f"  ✓ plan {plan.code}"))

        return plans

    # ------------------------------------------------------------------ #
    # Customers, subscriptions, invoices, usage  (--full only)           #
    # ------------------------------------------------------------------ #

    def _seed_customers_and_subscriptions(self, community, asset, plans, accounts):
        from django.contrib.auth import get_user_model
        from toto.people.models import Person
        from toto.subscriptions.models import (
            Subscription,
            SubscriptionInvoice,
            SubscriptionInvoiceLine,
        )
        from toto.subscriptions.services import (
            create_subscription,
            get_or_create_customer,
        )

        self.stdout.write("\n👥  Seeding demo customers and subscriptions…")
        User = get_user_model()

        demo_people = [
            ("sub-alice", "Alice Demo",  "sub-billing-alice"),
            ("sub-bob",   "Bob Demo",    "sub-billing-bob"),
            ("sub-carol", "Carol Demo",  "sub-billing-carol"),
            ("sub-dave",  "Dave Demo",   "sub-billing-dave"),
        ]
        customers = {}
        for username, display, billing_key in demo_people:
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@demo.toto"},
            )
            person, _ = Person.objects.get_or_create(
                display_name=display,
                defaults={"user": user},
            )
            customer = get_or_create_customer(
                community=community,
                person=person,
                billing_account=accounts[billing_key],
                default_asset=asset,
            )
            customers[username] = customer
            self.stdout.write(f"  + customer {display}")

        # (username, plan_code, interval, status_override, pay_invoice)
        scenarios = [
            ("sub-alice", "ai-starter",      "monthly", None,         True),
            ("sub-alice", "storage-100gb",   "yearly",  None,         True),
            ("sub-bob",   "ai-pro",          "monthly", None,         True),
            ("sub-bob",   "academy-pass",    "monthly", None,         False),  # open invoice
            ("sub-carol", "ai-starter",      "monthly", "trialing",   False),
            ("sub-carol", "community-basic", "monthly", None,         True),
            ("sub-dave",  "support-pro",     "monthly", "past_due",   False),
            ("sub-dave",  "academy-pass",    "monthly", "cancelled",  False),
        ]

        now = timezone.now()
        for username, plan_code, interval, status_override, pay_it in scenarios:
            customer = customers[username]
            plan = plans.get(plan_code)
            if not plan:
                continue
            price = plan.prices.filter(code=interval).first()
            if not price:
                continue
            if Subscription.objects.filter(customer=customer, plan=plan).exists():
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing {username} → {plan_code}"))
                continue

            sub = create_subscription(customer=customer, plan=plan, price=price, invoice_now=False)

            if status_override:
                Subscription.objects.filter(pk=sub.pk).update(status=status_override)
                sub.refresh_from_db()

            invoice = SubscriptionInvoice.objects.create(
                subscription=sub,
                customer=customer,
                price=price,
                status=SubscriptionInvoice.Status.OPEN,
                period_start=now,
                period_end=sub.current_period_end or now,
                due_at=now,
                subtotal_base_units=price.amount_base_units,
                total_base_units=price.amount_base_units,
            )
            SubscriptionInvoiceLine.objects.create(
                invoice=invoice,
                kind=SubscriptionInvoiceLine.LineKind.SUBSCRIPTION,
                description=f"{plan.name} – {price.get_interval_display()}",
                quantity=Decimal("1"),
                unit_amount_base_units=price.amount_base_units,
                amount_base_units=price.amount_base_units,
            )

            if pay_it:
                SubscriptionInvoice.objects.filter(pk=invoice.pk).update(
                    status=SubscriptionInvoice.Status.PAID,
                    paid_base_units=price.amount_base_units,
                )
            elif status_override == "past_due":
                SubscriptionInvoice.objects.filter(pk=invoice.pk).update(
                    status=SubscriptionInvoice.Status.FAILED,
                    error_message="Insufficient balance.",
                )

            label = status_override or "active"
            self.stdout.write(
                self.style.SUCCESS(f"  ✓ {username} → {plan_code} [{label}]")
            )

        self._seed_usage(customers, plans)

    def _seed_usage(self, customers, plans):
        from toto.subscriptions.models import Subscription
        from toto.subscriptions.services import record_usage

        usage_specs = [
            ("sub-alice", "ai-starter",    "ai.input_tokens",  Decimal("45000"),  "tokens"),
            ("sub-alice", "ai-starter",    "ai.input_tokens",  Decimal("82300"),  "tokens"),
            ("sub-alice", "ai-starter",    "ai.output_tokens", Decimal("12000"),  "tokens"),
            ("sub-alice", "storage-100gb", "storage.bytes",    Decimal("14.50"),  "GB"),
            ("sub-bob",   "ai-pro",        "ai.input_tokens",  Decimal("350000"), "tokens"),
            ("sub-bob",   "ai-pro",        "ai.output_tokens", Decimal("48000"),  "tokens"),
            ("sub-carol", "community-basic","community.posts", Decimal("73"),      "posts"),
        ]
        self.stdout.write("\n📊  Seeding demo usage records…")
        for username, plan_code, feature_code, qty, unit in usage_specs:
            customer = customers.get(username)
            plan = plans.get(plan_code)
            if not customer or not plan:
                continue
            sub = Subscription.objects.filter(
                customer=customer, plan=plan,
                status__in=["active", "trialing"],
            ).first()
            if not sub:
                continue
            try:
                record_usage(sub, feature_code, qty, unit=unit)
                self.stdout.write(f"  + usage {username}/{plan_code} {feature_code} {qty}")
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"  ⚠ usage {username}/{plan_code}: {exc}"))
