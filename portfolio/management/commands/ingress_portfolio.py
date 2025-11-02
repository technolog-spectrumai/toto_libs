from oya.ingress import IngressCommand
from portfolio.models import Chamber, Currency, Venture, Transaction, Company, Shareholder
from django.contrib.auth.models import User
from django.utils.timezone import now
from datetime import timedelta
import random
from faker import Faker

fake = Faker()

class Command(IngressCommand):
    help = "Seed sample data for SpectrumAi.pl Chamber: ventures, currencies, transactions, companies, shareholders"

    def process(self, _):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Portfolio",
            icon="fa-solid fa-briefcase",
            description="Portfolio of ventures and investments",
            link="/portfolio/"
        )
        currency_data = [
            ('USD', 'US Dollar', False),
            ('PLN', 'Polish Zloty', False)
        ]
        if self.full:
            currency_data.extend([
                ('BTC', 'Bitcoin', True),
                ('ETH', 'Ethereum', True),
                ('EUR', 'Euro', False),
                ('GBP', 'British Pound', False)
            ])

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
        default_currency = Currency.objects.get(symbol="PLN")
        active_chambers = Chamber.objects.filter(active=True)
        if active_chambers.count() == 0:
            chamber = Chamber.objects.create(
                name="SpectrumAi.pl",
                active=True,
                default_currency=default_currency
            )
            self.stdout.write(self.style.SUCCESS("🏛️ Created active chamber: SpectrumAi.pl"))
        elif active_chambers.count() == 1:
            chamber = active_chambers.first()
        else:
            raise Exception("❌ Multiple active chambers detected. Only one chamber can be active at a time.")

        if not self.full:
            return
        # 🏢 Create companies if fewer than 3 exist
        companies = list(Company.objects.all())
        while len(companies) < 3:
            company = Company.objects.create(
                name=fake.unique.company(),
                registration_number=fake.unique.bothify(text='??#####'),
                country=fake.country(),
                industry=fake.job(),
                date_founded=fake.date_between(start_date='-10y', end_date='-1y'),
                is_active=True
            )
            companies.append(company)
            self.stdout.write(self.style.SUCCESS(f"🏢 Created company: {company.name}"))

        # 📦 Create ventures linked to 3 random companies
        selected_companies = random.sample(companies, 3)
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

        # 💸 Add transactions (funding rounds)
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

        # 👤 Add shareholders and share transactions
        users = list(User.objects.all())
        for company in companies:
            for _ in range(random.randint(3, 6)):
                user = random.choice(users) if users and random.random() < 0.7 else None
                shareholder = Shareholder.objects.create(
                    company=company,
                    full_name=fake.name(),
                    email=fake.unique.email(),
                    shares_owned=random.randint(100, 1000),
                    date_joined=fake.date_between(start_date='-2y', end_date='today'),
                    is_active=True,
                    user=user
                )

        self.stdout.write(self.style.SUCCESS("✅ SpectrumAi.pl ingress complete."))
