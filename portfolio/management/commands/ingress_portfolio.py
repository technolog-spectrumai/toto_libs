from oya.ingress import IngressCommand
from portfolio.models import Chamber, Venture, Company, Shareholder, FundingRound
from finance.models import Subject, Currency
from community.models import SocialEntity
from django.utils.timezone import now
from datetime import timedelta
import random
from faker import Faker

fake = Faker()

class Command(IngressCommand):
    help = "Seed sample data for SpectrumAi.pl Chamber: ventures, companies, shareholders, and funding rounds"

    # -----------------------------
    # Helpers
    # -----------------------------
    def get_chamber(self):
        """
        Ensure there is an active Chamber with a default currency.
        """
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
        return chamber

    def create_companies(self, count=3):
        """
        Ensure at least `count` companies exist.
        """
        companies = list(Company.objects.all())
        while len(companies) < count:
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
        return companies


    def create_funding_rounds(self, companies, currency):
        """
        Create funding rounds for each venture.
        """
        for venture in companies:
            for i in range(random.randint(1, 3)):
                amount = round(random.uniform(5000, 50000), 2)
                timestamp = now() - timedelta(days=random.randint(1, 180))
                round_name = f"{venture.name} Round {i+1}"
                FundingRound.objects.create(
                    venture=venture,
                    name=round_name,
                    amount=amount,
                    currency=currency,
                    timestamp=timestamp
                )
                self.stdout.write(self.style.SUCCESS(
                    f"💸 Created funding round: {round_name} ({amount} {currency.symbol})"
                ))

    def create_shareholders(self, companies):
        """
        Create shareholders for each company.
        """
        for company in companies:
            for _ in range(random.randint(3, 6)):
                social_entity = SocialEntity.objects.create(name=fake.name())
                shareholder = Shareholder.objects.create(
                    company=company,
                    full_name=social_entity.name,
                    email=fake.unique.email(),
                    shares_owned=random.randint(100, 1000),
                    date_joined=fake.date_between(start_date='-2y', end_date='today'),
                    is_active=True,
                    social_entity=social_entity
                )
                self.stdout.write(self.style.SUCCESS(
                    f"👤 Created shareholder: {shareholder.full_name} in {company.name}"
                ))

    # -----------------------------
    # Main process
    # -----------------------------
    def process(self, _):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Portfolio",
            icon="fa-solid fa-briefcase",
            description="Portfolio of ventures and investments",
            link="/portfolio/"
        )

        # Step 1: Chamber
        chamber = self.get_chamber()

        if not self.full:
            return

        # Step 2: Companies
        companies = self.create_companies(count=3)

        # Step 4: Funding Rounds
        self.create_funding_rounds(companies, chamber.default_currency)

        # Step 5: Shareholders
        self.create_shareholders(companies)

        self.stdout.write(self.style.SUCCESS("✅ SpectrumAi.pl ingress complete with funding rounds and shareholders."))
