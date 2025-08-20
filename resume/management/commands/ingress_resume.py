from django.core.management.base import BaseCommand
from resume.models import Resume, WorkExperience, Education, Distinction, Language, Skill
import random
from faker import Faker

fake = Faker()

class Command(BaseCommand):
    help = 'Populate the database with dummy resume data'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting dummy data generation...")

        for _ in range(10):  # Create 10 resumes
            resume = Resume.objects.create(
                full_name=fake.name(),
                phone_number=fake.phone_number(),
                email=fake.email(),
                citizenship=fake.country()
            )

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

        self.stdout.write(self.style.SUCCESS("Dummy resumes successfully created!"))
