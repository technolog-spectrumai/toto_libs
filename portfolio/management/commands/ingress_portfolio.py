from oya.ingress import IngressCommand
from portfolio.models import Chamber, Venture, Company, Shareholder, FundingRound
from finance.models import Subject, Currency
from community.models import SocialEntity
from django.contrib.auth.models import User
from django.utils.timezone import now
from datetime import timedelta
import random
from faker import Faker

fake = Faker()

class Command(IngressCommand):
    help = "Seed sample data for SpectrumAi.pl Chamber: ventures, companies, shareholders, and funding rounds"

    def process(self, _):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Portfolio",
            icon="fa-solid fa-briefcase",
            description="Portfolio of ventures and investments",
            link="/portfolio/"
        )

        # 🏛️ Ensure active chamber
        chamber = Chamber.objects.filter(active=True).first()
        if not chamber:
            default_currency = Currency.objects.filter(active=True).first()
            if not default_currency:
                raise Exception("❌ No active currencies found. Please create one manually before initializing the chamber.")
            chamber = Chamber.objects.create(
                name="SpectrumAi.pl",
                active=True,
                default_currency=default_currency
            )
            self.stdout.write(self.style.SUCCESS(f"🏛️ Created active chamber: {chamber.name}"))

        if not self.full:
            return

        # 🏢 Create companies
        companies = list(Company.objects.all())
        while len(companies) < 3:
            social_entity = SocialEntity.objects.create(name=fake.unique.company())
            company = Company.objects.create(
                name=social_entity.name,
                registration_number=fake.unique.bothify(text='??#####'),
                country=fake.country(),
                industry=fake.job(),
                date_founded=fake.date_between(start_date='-10y', end_date='-1y'),
                is_active=True,
                social_entity=social_entity
            )
            Subject.objects.get_or_create(
                social_entity=social_entity,
                defaults={
                    'name': company.name,
                    'legal_type': 'corporation',
                    'identifier': company.registration_number,
                    'active': True
                }
            )
            companies.append(company)
            self.stdout.write(self.style.SUCCESS(f"🏢 Created company: {company.name}"))

        # 📦 Create ventures
        selected_companies = random.sample(companies, 3)
        ventures = []
        for company in selected_companies:
            venture_name = f"{company.name} Venture"
            venture_url = f"https://example.com/{company.name.lower().replace(' ', '-')}"
            venture = Venture.objects.create(
                name=venture_name,
                url=venture_url,
                start=now(),
                company=company
            )
            ventures.append(venture)
            self.stdout.write(self.style.SUCCESS(f"📦 Created venture: {venture.name}"))

        # 💸 Create funding rounds
        for venture in ventures:
            for i in range(random.randint(1, 3)):
                amount = round(random.uniform(5000, 50000), 2)
                timestamp = now() - timedelta(days=random.randint(1, 180))
                round_name = f"{venture.name} Round {i+1}"
                FundingRound.objects.create(
                    venture=venture,
                    name=round_name,
                    amount=amount,
                    currency=chamber.default_currency,
                    timestamp=timestamp
                )
                self.stdout.write(self.style.SUCCESS(f"💸 Created funding round: {round_name} ({amount} {chamber.default_currency.symbol})"))

        # 👤 Create shareholders
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

        self.stdout.write(self.style.SUCCESS("✅ SpectrumAi.pl ingress complete with funding rounds."))
