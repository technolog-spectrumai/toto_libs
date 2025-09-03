import random
from faker import Faker
from resume.models import Resume, WorkExperience, Education, Distinction, Language, Skill
from django.utils.text import slugify
from django.contrib.auth.models import User
from oya.ingress import IngressCommand  # Your custom base class

fake = Faker()

class Command(IngressCommand):
    help = "Populate the database with dummy resume data for testing and demo purposes"

    def process(self, _):
        # Dashboard block
        self.create_dashboard_item(
            title="Resume Ingress",
            icon="id-card",
            description="Creates sample resumes with work experience, education, skills, and more.",
            link="/resumes/"
        )

        # Ensure demo user exists
        user, _ = User.objects.get_or_create(
            username='demo_user',
            defaults={'email': 'demo@example.com'}
        )

        self.stdout.write(self.style.SUCCESS("👤 Using demo user for resume generation"))

        for i in range(10):  # Create 10 resumes
            full_name = fake.name()
            resume = Resume.objects.create(
                full_name=full_name,
                phone_number=fake.phone_number(),
                email=fake.email(),
                citizenship=fake.country(),
                owner=user
            )
            self.stdout.write(self.style.SUCCESS(f"📄 Created resume: {full_name}"))

            # Work Experience
            for _ in range(random.randint(1, 3)):
                WorkExperience.objects.create(
                    resume=resume,
                    start_date=fake.date(),
                    end_date=fake.date(),
                    title=fake.job(),
                    company=fake.company(),
                    location=fake.city(),
                    description=fake.text(max_nb_chars=300)
                )

            # Education
            for _ in range(random.randint(1, 2)):
                Education.objects.create(
                    resume=resume,
                    start_date=fake.date(),
                    end_date=fake.date(),
                    degree=random.choice(['BSc', 'MSc', 'PhD']),
                    institution=fake.company() + " University",
                    specialization=fake.word()
                )

            # Distinctions
            for _ in range(random.randint(0, 2)):
                Distinction.objects.create(
                    resume=resume,
                    date_awarded=fake.date(),
                    title=fake.catch_phrase()
                )

            # Languages
            for lang in random.sample(['English', 'Polish', 'German', 'Spanish', 'French'], k=2):
                Language.objects.create(
                    resume=resume,
                    name=lang,
                    proficiency=random.choice(['native', 'fluent', 'basic'])
                )

            # Skills
            for _ in range(random.randint(2, 5)):
                Skill.objects.create(
                    resume=resume,
                    category=random.choice(['Programming', 'Design', 'Management', 'Communication']),
                    description=fake.sentence()
                )

            self.stdout.write(self.style.SUCCESS(f"🧩 Added experience, education, and skills to: {full_name}"))

        self.stdout.write(self.style.SUCCESS("✅ Resume ingress complete."))
