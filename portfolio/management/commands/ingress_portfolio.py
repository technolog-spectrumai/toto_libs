from django.contrib.auth.models import User
from django.utils.text import slugify
from oya.ingress import IngressCommand
from portfolio.models import (
    Investor, Portfolio, Asset, Currency,
    Transaction, Event, Milestone
)
import random
from datetime import timedelta, date
from django.utils.timezone import now


class Command(IngressCommand):
    help = "Seed sample data for Portfolio app: investors, portfolios, assets, currencies, transactions, events"

    def process(self, _):
        # Dashboard block
        self.create_dashboard_item(
            title="Portfolio Demo",
            icon="fa-solid fa-briefcase",
            description="Seeds sample investors, portfolios, assets, currencies, and transactions for demo/testing.",
            link="/portfolio/"
        )

        # Ensure demo user and investor
        user, _ = User.objects.get_or_create(
            username='demo_investor',
            defaults={'email': 'investor@example.com'}
        )
        investor, _ = Investor.objects.get_or_create(
            user=user,
            defaults={
                'display_name': 'Demo Investor',
                'wallet_address': '0xDEMO123456789',
                'balance': 100000,
                'kyc_verified': True
            }
        )

        # Create currencies
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
                    'is_internal': symbol == 'VC',
                    'decimals': 8,
                    'active': True
                }
            )
            currencies.append(currency)

        # Create portfolios
        portfolio_data = [
            ('Alpha Growth', 'Aggressive crypto strategy'),
            ('Stable Yield', 'Conservative yield farming'),
            ('Tech Picks', 'Focused on blockchain infrastructure')
        ]
        for title, strategy in portfolio_data:
            if Portfolio.objects.filter(name=title, investor=investor).exists():
                self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing portfolio: {title}"))
                continue

            portfolio = Portfolio.objects.create(
                investor=investor,
                name=title,
                strategy=strategy
            )
            self.stdout.write(self.style.SUCCESS(f"💼 Created portfolio: {title}"))

            # Add assets
            for currency in random.sample(currencies, k=3):
                Asset.objects.create(
                    portfolio=portfolio,
                    name=currency.name,
                    symbol=currency.symbol,
                    quantity=random.uniform(1, 100)
                )

            # Add milestone
            Milestone.objects.create(
                portfolio=portfolio,
                title=f"{title} Launch",
                category="Launch",
                achieved=False,
                target_date=date.today() + timedelta(days=30)
            )

        # Add transactions
        for _ in range(5):
            Transaction.objects.create(
                investor=investor,
                currency=random.choice(currencies),
                amount=random.uniform(500, 5000),
                reason="Initial funding"
            )

        # Add events
        for i in range(3):
            Event.objects.create(
                owner=investor,
                title=f"Strategy Update {i+1}",
                event_type="Strategy Update",
                severity=random.choice(['INFO', 'WARNING']),
                metadata={"status": "planned", "note": "Demo event"}
            )

        self.stdout.write(self.style.SUCCESS("✅ Portfolio ingress complete."))
