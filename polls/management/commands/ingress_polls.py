from django.utils import timezone
from django.contrib.auth import get_user_model
from django.urls import reverse
from oya.ingress import IngressCommand
from faker import Faker
import random

from polls.models import Question, Choice, Answer

fake = Faker()
User = get_user_model()


class Command(IngressCommand):
    help = "Seeds demo poll questions, choices, and answers."

    def process(self):

        # ---------------------------------------------------------
        # DASHBOARD ITEM
        # ---------------------------------------------------------
        self.create_dashboard_item(
            title="Polls",
            icon="fa-solid fa-square-poll-vertical",
            description="Interactive polls with questions, choices, and user answers.",
            link=reverse("polls:poll_list"),
            public=False,
        )

        if not self.full:
            return

        # ---------------------------------------------------------
        # SAFETY: Prevent duplicate demo data
        # ---------------------------------------------------------
        if Question.objects.filter(name="favorite_color").exists():
            print("[Ingress] Poll demo already exists — skipping.")
            return

        # ---------------------------------------------------------
        # REQUIRE USERS
        # ---------------------------------------------------------
        users = list(User.objects.all())
        if not users:
            raise Exception("❌ Need at least 1 User to seed poll answers.")

        # ---------------------------------------------------------
        # CREATE QUESTIONS
        # ---------------------------------------------------------
        q1 = Question.objects.create(
            name="favorite_color",
            question_text="What is your favorite color",
            pub_date=timezone.now(),
            is_active=True,
        )

        q2 = Question.objects.create(
            name="satisfaction_level",
            question_text="How satisfied are you with our service",
            pub_date=timezone.now(),
            is_active=True,
        )

        q3 = Question.objects.create(
            name="daily_coffee",
            question_text="How many cups of coffee do you drink per day",
            pub_date=timezone.now(),
            is_active=False,
        )

        # ---------------------------------------------------------
        # CHOICES FOR Q1
        # ---------------------------------------------------------
        c1_red = Choice.objects.create(question=q1, label="A", choice_text="Red", value=10)
        c1_blue = Choice.objects.create(question=q1, label="B", choice_text="Blue", value=20)
        c1_green = Choice.objects.create(question=q1, label="C", choice_text="Green", value=30)

        # ---------------------------------------------------------
        # CHOICES FOR Q2
        # ---------------------------------------------------------
        c2_low = Choice.objects.create(question=q2, label="A", choice_text="Low", value=1)
        c2_med = Choice.objects.create(question=q2, label="B", choice_text="Medium", value=2)
        c2_high = Choice.objects.create(question=q2, label="C", choice_text="High", value=3)

        # ---------------------------------------------------------
        # CHOICES FOR Q3
        # ---------------------------------------------------------
        c3_none = Choice.objects.create(question=q3, label="A", choice_text="0 cups", value=0)
        c3_one = Choice.objects.create(question=q3, label="B", choice_text="1 cup", value=1)
        c3_many = Choice.objects.create(question=q3, label="C", choice_text="2+ cups", value=2)

        # ---------------------------------------------------------
        # RANDOM ANSWERS
        # ---------------------------------------------------------
        all_questions = [q1, q2, q3]
        all_choices = {
            q1: [c1_red, c1_blue, c1_green],
            q2: [c2_low, c2_med, c2_high],
            q3: [c3_none, c3_one, c3_many],
        }

        for user in users:
            for question in all_questions:
                # Only answer active polls
                if not question.is_active:
                    continue

                choice = random.choice(all_choices[question])
                Answer.objects.create(
                    user=user,
                    question=question,
                    choice=choice,
                )

        print("[Ingress] Demo polls created successfully.")
