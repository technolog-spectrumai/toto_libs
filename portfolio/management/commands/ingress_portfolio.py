from django.contrib.auth.models import User
from oya.ingress import IngressCommand
from portfolio.models import Chamber, Currency, Venture, Transaction
from shareholders.models import Company
import random
from datetime import timedelta
from django.utils.timezone import now
from faker import Faker

fake = Faker()

class Command(IngressCommand):
    help = "Seed sample data for SpectrumAi.pl Chamber: ventures, currencies, transactions, and events"

    def process(self, _):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="SpectrumAi.pl Demo",
            icon="fa-solid fa-briefcase",
            description="Seeds sample ventures, currencies, transactions, and events for demo/testing.",
            link="/portfolio/"
        )

        # 💱 Create currencies
        currency_data = [
            ('BTC', 'Bitcoin', True),
            ('ETH', 'Ethereum', True),
            ('USD', 'US Dollar', False),
            ('VC', 'Virtual Credit', False)
        ]
        currencies = []
        for symbol, name, is_crypto in currency_data:
            currency, _ = Currency.objects.get_or_create(
                symbol=symbol,
                defaults={
                    'name': name,
                    'is_crypto': is_crypto,
                    'decimals': 8,
                    'active': True
                }
            )
            currencies.append(currency)

        # 🏛️ Ensure active chamber
        usd_currency = Currency.objects.get(symbol="USD")
        active_chambers = Chamber.objects.filter(active=True)
        if active_chambers.count() == 0:
            chamber = Chamber.objects.create(
                name="SpectrumAi.pl",
                manifest="Multi-chain crypto growth",
                strategy="Long-term decentralized innovation",
                active=True,
                default_currency=usd_currency
            )
            self.stdout.write(self.style.SUCCESS("🏛️ Created active chamber: SpectrumAi.pl"))
        elif active_chambers.count() == 1:
            chamber = active_chambers.first()
        else:
            raise Exception("❌ Multiple active chambers detected. Only one chamber can be active at a time.")

        # 🏢 Select 3 random companies
        all_companies = list(Company.objects.all())
        if len(all_companies) < 3:
            raise Exception("❌ Not enough companies to seed ventures. Please create at least 3 companies first.")

        selected_companies = random.sample(all_companies, 3)

        # 📦 Create ventures linked to selected companies
        ventures = []
        for company in selected_companies:
            venture_name = f"{company.name} Venture"
            venture_url = f"https://example.com/{company.name.lower().replace(' ', '-')}"
            venture, created = Venture.objects.get_or_create(
                name=venture_name,
                defaults={
                    'url': venture_url,
                    'start': now(),
                    'company': company
                }
            )
            ventures.append(venture)
            if created:
                self.stdout.write(self.style.SUCCESS(f"📦 Created venture: {venture_name} (Company: {company.name})"))

        # 🔁 Add transactions (funding rounds)
        for venture in ventures:
            for i in range(2):
                currency = random.choice(currencies)
                amount = round(random.uniform(1000, 10000), 2)
                Transaction.objects.create(
                    name=f"{venture.name} Round {i+1}",
                    amount=amount,
                    currency=currency,
                    venture=venture,
                    timestamp=now() - timedelta(days=random.randint(1, 30))
                )

        self.stdout.write(self.style.SUCCESS("✅ SpectrumAi.pl ingress complete."))
