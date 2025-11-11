from oya.ingress import IngressCommand
from finance.models import Currency, Account, Transaction, ExchangeRate, Subject
from community.models import CommunityMember
from django.contrib.auth.models import User
from django.utils.timezone import now
from datetime import timedelta
import random
from faker import Faker

fake = Faker()


class Command(IngressCommand):
    help = "Seed sample data for finance: currencies, accounts held by community members, transactions"

    def create_currencies(self):
        currency_data = [
            ('USD', 'US Dollar', False),
            ('PLN', 'Polish Zloty', False)
        ]
        if self.full:
            currency_data.extend([
                ('BTC', 'Bitcoin', True),
                ('ETH', 'Ethereum', True),
                ('EUR', 'Euro', False)
            ])

        currencies = []
        for symbol, name, is_crypto in currency_data:
            currency, _ = Currency.objects.get_or_create(
                symbol=symbol,
                defaults={"name": name, "is_crypto": is_crypto}
            )
            currencies.append(currency)

        # Exchange rates
        exchange_pairs = [
            ("USD", "PLN", 4.25),
            ("EUR", "USD", 1.08),
            ("BTC", "USD", 35000),
            ("ETH", "USD", 1900),
            ("PLN", "EUR", 0.23),
        ]
        for base_symbol, quote_symbol, rate in exchange_pairs:
            base = next((c for c in currencies if c.symbol == base_symbol), None)
            quote = next((c for c in currencies if c.symbol == quote_symbol), None)
            if base and quote:
                ExchangeRate.objects.create(
                    base_currency=base,
                    quote_currency=quote,
                    rate=rate,
                    timestamp=now()
                )

        return currencies

    def create_accounts(self, members, currencies):
        accounts = []
        users = list(User.objects.all())
        if not users:
            for i in range(3):
                user, _ = User.objects.get_or_create(
                    username=f"user{i}",
                    defaults={"email": f"user{i}@example.com"}
                )
                users.append(user)

        for i in range(10):
            holder = random.choice(members)

            # ✅ CommunityMember is itself a SocialEntity now
            subject = getattr(holder, "subject", None)
            if not subject:
                subject = Subject.objects.create(
                    social_entity=holder,   # use the member directly
                    name=holder.display_name,
                    legal_type="individual",
                    identifier=fake.uuid4()[:8],
                    contact_info=fake.email()
                )

            account = Account.objects.create(
                name=fake.company(),
                owner=subject,
                manager=random.choice(users),
                currency=random.choice(currencies),
                balance=round(random.uniform(1000, 10000), 2)
            )
            accounts.append(account)
        return accounts

    def create_transactions(self, accounts):
        for i in range(15):
            src, dst = random.sample(accounts, 2)
            currency = src.currency
            Transaction.objects.create(
                name=f"Transaction {i+1}",
                amount=round(random.uniform(50, 500), 2),
                currency=currency,
                source=src,
                destination=dst,
                timestamp=now() - timedelta(days=random.randint(0, 30))
            )

    def process(self, _):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Finance",
            icon="fa-solid fa-coins",
            description="Accounts and transactions linked to community members",
            link="/finance/",
            public=False
        )

        # Step 1: currencies
        currencies = self.create_currencies()

        if not self.full:
            return

        members = list(CommunityMember.objects.all())
        if len(members) == 0:
            raise ValueError("No Members found to assign as account holders.")

        # Step 2: accounts
        accounts = self.create_accounts(members, currencies)

        # Step 3: transactions
        self.create_transactions(accounts)

        self.stdout.write(self.style.SUCCESS("✅ Finance data seeded successfully with CommunityMember account holders."))
