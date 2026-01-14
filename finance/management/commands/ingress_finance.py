from oya.ingress import IngressCommand
from django.urls import reverse
from faker import Faker
import random
from decimal import Decimal

from federal.models import IdentityProfile
from community.models import CommunityMember
from finance.models import Currency, Account, Transaction

fake = Faker()


class Command(IngressCommand):
    help = "Seeds demo currencies, accounts, and transactions."

    def process(self):

        # ---------------------------------------------------------
        # DASHBOARD ITEM
        # ---------------------------------------------------------
        self.create_dashboard_item(
            title="Finance",
            icon="fa-solid fa-coins",
            description="Accounts, currencies, and financial transactions.",
            link=reverse("finance:account_list"),
            public=False,
        )

        if not self.full:
            return

        # ---------------------------------------------------------
        # SAFETY: Prevent duplicate demo data
        # ---------------------------------------------------------
        if Currency.objects.filter(symbol="USD").exists():
            print("[Ingress] Finance demo already exists — skipping.")
            return

        # ---------------------------------------------------------
        # REQUIREMENTS: Need at least 1 IdentityProfile + CommunityMember
        # ---------------------------------------------------------
        profiles = list(IdentityProfile.objects.all())
        members = list(CommunityMember.objects.all())

        if not profiles:
            raise Exception("❌ Need at least 1 IdentityProfile to seed finance.")
        if not members:
            raise Exception("❌ Need at least 1 CommunityMember to seed finance.")

        # Pick random owners
        owner1 = random.choice(profiles)
        owner2 = random.choice(profiles)
        manager = random.choice(members).user if random.choice(members).user else None

        # ---------------------------------------------------------
        # CURRENCIES
        # ---------------------------------------------------------
        usd = Currency.objects.create(
            symbol="USD",
            name="US Dollar",
            is_crypto=False,
            decimals=2,
            active=True,
        )

        eur = Currency.objects.create(
            symbol="EUR",
            name="Euro",
            is_crypto=False,
            decimals=2,
            active=True,
        )

        btc = Currency.objects.create(
            symbol="BTC",
            name="Bitcoin",
            is_crypto=True,
            decimals=8,
            active=True,
        )

        # ---------------------------------------------------------
        # ACCOUNTS
        # ---------------------------------------------------------
        account_usd = Account.objects.create(
            name="Main USD Account",
            owner=owner1,
            manager=manager,
            currency=usd,
            balance=Decimal("5000.00"),
        )

        account_eur = Account.objects.create(
            name="European Reserve",
            owner=owner2,
            manager=manager,
            currency=eur,
            balance=Decimal("3200.00"),
        )

        account_btc = Account.objects.create(
            name="Crypto Vault",
            owner=owner1,
            manager=manager,
            currency=btc,
            balance=Decimal("0.75000000"),
        )

        # ---------------------------------------------------------
        # TRANSACTIONS
        # ---------------------------------------------------------
        # USD → EUR (with conversion)
        Transaction.objects.create(
            name="USD to EUR Transfer",
            amount=Decimal("100.00"),
            source=account_usd,
            destination=account_eur,
            conversion_rate=Decimal("0.92"),
        ).execute()

        # EUR → USD (reverse)
        Transaction.objects.create(
            name="EUR to USD Transfer",
            amount=Decimal("50.00"),
            source=account_eur,
            destination=account_usd,
            conversion_rate=Decimal("1.08"),
        ).execute()

        # USD → BTC (crypto purchase)
        Transaction.objects.create(
            name="BTC Purchase",
            amount=Decimal("200.00"),
            source=account_usd,
            destination=account_btc,
            conversion_rate=Decimal("0.000025"),
        ).execute()

        # Same-currency transfer
        Transaction.objects.create(
            name="Internal USD Transfer",
            amount=Decimal("75.00"),
            source=account_usd,
            destination=account_usd,  # self-transfer demo
            conversion_rate=Decimal("1.0"),
        ).execute()

        print("[Ingress] Demo finance registry created successfully.")
