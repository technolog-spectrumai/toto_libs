from oya.ingress import IngressCommand
from finance.models import Currency, Subject, Account, Transaction, ExchangeRate
from community.models import SocialEntity
from django.contrib.auth.models import User
from django.utils.timezone import now
from datetime import timedelta
import random
from faker import Faker

fake = Faker()

class Command(IngressCommand):
    help = "Seed sample data for finance: currencies, subjects, accounts, transactions, obligations"

    def process(self, _):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Finance",
            icon="fa-solid fa-coins",
            description="Accounts, transactions, obligations, and financial subjects",
            link="/finance/"
        )

        # 💱 Currencies
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
            currency, _ = Currency.objects.get_or_create(symbol=symbol, defaults={"name": name, "is_crypto": is_crypto})
            currencies.append(currency)

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

        if not self.full:
            return
        # 🧍 Subjects linked to existing SocialEntities
        subjects = []
        for _ in range(5):
            entity_name = fake.name()
            social_entity, _ = SocialEntity.objects.get_or_create(name=entity_name)
            subject, created = Subject.objects.get_or_create(
                social_entity=social_entity,
                defaults={
                    "name": entity_name,
                    "legal_type": random.choice(['individual', 'corporation']),
                    "identifier": fake.uuid4()[:8],
                    "contact_info": fake.email()
                }
            )
            subjects.append(subject)

        # 👤 Users
        users = []
        for i in range(3):
            user, _ = User.objects.get_or_create(username=f"user{i}", defaults={"email": f"user{i}@example.com"})
            users.append(user)

        # 🏦 Accounts
        accounts = []
        for i in range(10):
            account = Account.objects.create(
                name=fake.company(),
                owner=random.choice(subjects),
                manager=random.choice(users),
                currency=random.choice(currencies),
                balance=round(random.uniform(1000, 10000), 2)
            )
            accounts.append(account)

        # 💸 Transactions
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

        self.stdout.write(self.style.SUCCESS("✅ Finance data seeded successfully."))
