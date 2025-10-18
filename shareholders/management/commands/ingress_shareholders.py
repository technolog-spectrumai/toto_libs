from datetime import timedelta

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from shareholders.models import Company, Shareholder, ShareTransaction
from django.utils.timezone import now
import random
from faker import Faker

fake = Faker()

class Command(BaseCommand):
    help = "Seed sample companies, shareholders, and share transactions for demo/testing."

    def handle(self, *args, **kwargs):
        self.stdout.write("🌱 Seeding companies and shareholders...")

        # Create sample companies
        companies = []
        for _ in range(3):
            company = Company.objects.create(
                name=fake.company(),
                registration_number=fake.unique.bothify(text='??#####'),
                country=fake.country(),
                industry=fake.job(),
                date_founded=fake.date_between(start_date='-10y', end_date='-1y'),
                is_active=True
            )
            companies.append(company)

        # Create sample users
        users = list(User.objects.all())
        if not users:
            for i in range(5):
                users.append(User.objects.create_user(
                    username=f"user{i}",
                    email=f"user{i}@example.com",
                    password="password123"
                ))

        # Create shareholders
        for company in companies:
            for _ in range(random.randint(3, 6)):
                user = random.choice(users) if random.random() < 0.7 else None
                shareholder = Shareholder.objects.create(
                    company=company,
                    full_name=fake.name(),
                    email=fake.unique.email(),
                    shares_owned=random.randint(100, 1000),
                    date_joined=fake.date_between(start_date='-2y', end_date='today'),
                    is_active=True,
                    user=user
                )

                # Create share transactions
                for _ in range(random.randint(1, 4)):
                    ShareTransaction.objects.create(
                        shareholder=shareholder,
                        transaction_date=now() - timedelta(days=random.randint(1, 365)),
                        shares_changed=random.choice([50, -30, 100, -20]),
                        notes=fake.sentence()
                    )

        self.stdout.write(self.style.SUCCESS("✅ Shareholders and companies seeded successfully."))
