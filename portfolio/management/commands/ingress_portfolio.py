from django.contrib.auth.models import User
from oya.ingress import IngressCommand
from portfolio.models import (
    Chamber, Investor, Currency, Venture,
    Transaction
)
import random
from datetime import timedelta
from django.utils.timezone import now


class Command(IngressCommand):
    help = "Seed sample data for SpectrumAi.pl Chamber: investors, ventures, currencies, transactions, and events"

    def process(self, _):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="SpectrumAi.pl Demo",
            icon="fa-solid fa-briefcase",
            description="Seeds sample investors, ventures, currencies, transactions, and events for demo/testing.",
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
                strategy="Long-term decentralized innovation",
                active=True,
                default_currency=usd_currency,
                total_stock_emitted=1000000  # 1 million shares issued
            )
            self.stdout.write(self.style.SUCCESS("🏛️ Created active chamber: SpectrumAi.pl"))
        elif active_chambers.count() == 1:
            chamber = active_chambers.first()
        else:
            raise Exception("❌ Multiple active chambers detected. Only one chamber can be active at a time.")

        # 👤 Ensure demo user and investor
        user, _ = User.objects.get_or_create(
            username='demo_investor',
            defaults={'email': 'investor@example.com'}
        )
        investor, _ = Investor.objects.get_or_create(
            user=user,
            defaults={
                'display_name': 'Demo Investor',
                'chamber': chamber,
                'stock_owned': 250000  # owns 25% of total shares
            }
        )
        if investor.chamber != chamber:
            investor.chamber = chamber
            investor.save()

        # 📦 Create ventures
        venture_data = [
            ("Alpha Growth", "https://example.com/alpha"),
            ("Stable Yield", "https://example.com/stable"),
            ("Tech Picks", "https://example.com/tech")
        ]
        ventures = []
        for name, url in venture_data:
            venture, created = Venture.objects.get_or_create(
                name=name,
                defaults={
                    'url': url,
                    'start': now()
                }
            )
            ventures.append(venture)
            if created:
                self.stdout.write(self.style.SUCCESS(f"📦 Created venture: {name}"))

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
