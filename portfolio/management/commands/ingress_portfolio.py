import random
from django.utils.timezone import now
from datetime import timedelta
from faker import Faker
from oya.ingress import IngressCommand
from portfolio.models import Chamber, Company, SharePackage, FundingRound
from finance.models import Currency
from community.models import CommunityMember, SocialEntity


fake = Faker()


class Command(IngressCommand):
    help = "Seed sample data for SpectrumAi.pl Chamber: ventures, companies, share packages, and funding rounds"

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
            company = Company.objects.create(
                name=fake.unique.company(),
                registration_number=fake.unique.bothify(text='??#####'),
                country=fake.country(),
                industry=fake.job(),
                date_founded=fake.date_between(start_date='-10y', end_date='-1y'),
                is_active=True,
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

    def create_share_packages(self, companies):
        """
        Create shares for each company.
        """
        # Get all existing community members
        existing_members = list(CommunityMember.objects.all())

        for company in companies:
            for _ in range(random.randint(3, 6)):
                if existing_members:
                    # Pick a random existing member
                    member = random.choice(existing_members)
                else:
                    raise Exception("❌ No CommunityMembers found. Please create some before adding share packages.")

                share_package = SharePackage.objects.create(
                    company=company,
                    shares_owned=random.randint(100, 1000),
                    date_joined=fake.date_between(start_date='-2y', end_date='today'),
                    is_active=True,
                    social_entity=member  # compulsory link
                )
                self.stdout.write(self.style.SUCCESS(
                    f"👤 Linked shares: {share_package.get_full_name()} in {company.name}"
                ))

    # -----------------------------
    # Main process
    # -----------------------------
    def process(self):
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

        # Step 3: Funding Rounds
        self.create_funding_rounds(companies, chamber.default_currency)

        # Step 4: Shareholders
        self.create_share_packages(companies)

        self.stdout.write(self.style.SUCCESS("✅ SpectrumAi.pl ingress complete with funding rounds and shares."))
