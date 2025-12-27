from django.urls import reverse
from oya.ingress import IngressCommand

from portfolio.models import Company, FractionalOwnership
from locations.models import Address
from community.models import Persona

from faker import Faker
import random

fake = Faker()


class Command(IngressCommand):
    help = "Seed sample data for companies and fractional ownerships"

    # ---------------------------------------------------------
    # Create Companies
    # ---------------------------------------------------------
    def create_companies(self):
        base_companies = [
            "Acme Corporation",
            "Globex Industries",
            "Umbrella Holdings",
            "Wayne Enterprises",
            "Stark Innovations",
        ]

        if self.full:
            base_companies.extend([
                "Initech",
                "Hooli",
                "Massive Dynamic",
                "Wonka Industries",
            ])

        companies = []
        addresses = list(Address.objects.all())
        if not addresses:
            raise RuntimeError("No addresses found. Please seed addresses before seeding companies.")

        for name in base_companies:
            company, _ = Company.objects.get_or_create(
                name=name,
                defaults={
                    "registration_number": fake.bothify(text="REG-####-????"),
                    "founded_date": fake.date_between(start_date="-50y", end_date="-1y"),
                    "website": fake.url(),
                    "email": fake.company_email(),
                    "headquarters": random.choice(addresses),
                    "metadata": {
                        "industry": fake.word(),
                        "employees": random.randint(10, 5000),
                        "valuation": f"{random.randint(10, 500)}M USD",
                    },
                }
            )
            companies.append(company)

        return companies

    # ---------------------------------------------------------
    # Create Fractional Ownerships
    # ---------------------------------------------------------
    def create_ownerships(self, companies):
        """
        Any SocialEntity can be an owner, but only Company can be owned.
        """
        owners = list(Persona.objects.all())

        if not owners:
            raise RuntimeError("No Persona records found. Seed communities/members first.")

        for company in companies:
            # Each company gets 1–3 random owners
            num_owners = random.randint(1, 3)
            selected_owners = random.sample(owners, num_owners)

            remaining = 100.0
            for owner in selected_owners:
                if remaining <= 0:
                    break

                pct = round(random.uniform(5, remaining), 2)
                remaining -= pct

                FractionalOwnership.objects.get_or_create(
                    owner_entity=owner,
                    defaults={
                        "percentage": pct,
                        "metadata": {
                            "notes": fake.sentence(),
                            "agreement_id": fake.uuid4(),
                        }
                    }
                )

    # ---------------------------------------------------------
    # Main Process
    # ---------------------------------------------------------
    def process(self):
        # Dashboard block
        self.create_dashboard_item(
            title="Companies",
            icon="fa-solid fa-building",
            description="Companies and fractional ownership structure",
            link=reverse("portfolio:company_list"),
            public=False
        )

        # Step 1: Create companies
        companies = self.create_companies()

        if not self.full:
            return

        # Step 2: Create ownerships
        self.create_ownerships(companies)

        self.stdout.write(self.style.SUCCESS("🏢 Companies & ownerships seeded successfully."))
