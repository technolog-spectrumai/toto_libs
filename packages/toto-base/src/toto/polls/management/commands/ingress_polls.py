from django.utils import timezone
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.text import slugify
from toto.ingress import IngressCommand
from faker import Faker
import random
from toto.polls.models import Poll, Option, Vote

fake = Faker()
User = get_user_model()


class Command(IngressCommand):
    help = "Seeds demo polls, options, and votes."

    def process(self):

        # ---------------------------------------------------------
        # DASHBOARD ITEM
        # ---------------------------------------------------------

        if not self.full:
            return

        # ---------------------------------------------------------
        # SAFETY: Prevent duplicate demo data
        # ---------------------------------------------------------
        if Poll.objects.filter(name="favorite color").exists():
            print("[Ingress] Poll demo already exists — skipping.")
            return

        # ---------------------------------------------------------
        # REQUIRE USERS
        # ---------------------------------------------------------
        users = list(User.objects.all())
        if not users:
            raise Exception("❌ Need at least 1 User to seed poll votes.")

        # ---------------------------------------------------------
        # CREATE POLLS (with slug)
        # ---------------------------------------------------------
        p1 = self.create_poll(
            name="favorite color",
            question="What is your favorite color"
        )

        p2 = self.create_poll(
            name="satisfaction level",
            question="How satisfied are you with our service"
        )

        p3 = self.create_poll(
            name="daily coffee",
            question="How many cups of coffee do you drink per day",
            active=False
        )

        # ---------------------------------------------------------
        # OPTIONS FOR P1
        # ---------------------------------------------------------
        o1_red = Option.objects.create(poll=p1, label="A", option_text="Red", value=10)
        o1_blue = Option.objects.create(poll=p1, label="B", option_text="Blue", value=20)
        o1_green = Option.objects.create(poll=p1, label="C", option_text="Green", value=30)

        # ---------------------------------------------------------
        # OPTIONS FOR P2
        # ---------------------------------------------------------
        o2_low = Option.objects.create(poll=p2, label="A", option_text="Low", value=1)
        o2_med = Option.objects.create(poll=p2, label="B", option_text="Medium", value=2)
        o2_high = Option.objects.create(poll=p2, label="C", option_text="High", value=3)

        # ---------------------------------------------------------
        # OPTIONS FOR P3
        # ---------------------------------------------------------
        o3_none = Option.objects.create(poll=p3, label="A", option_text="0 cups", value=0)
        o3_one = Option.objects.create(poll=p3, label="B", option_text="1 cup", value=1)
        o3_many = Option.objects.create(poll=p3, label="C", option_text="2+ cups", value=2)

        # ---------------------------------------------------------
        # RANDOM VOTES
        # ---------------------------------------------------------
        all_polls = [p1, p2, p3]
        all_options = {
            p1: [o1_red, o1_blue, o1_green],
            p2: [o2_low, o2_med, o2_high],
            p3: [o3_none, o3_one, o3_many],
        }

        for user in users:
            for poll in all_polls:
                if not poll.is_active:
                    continue

                option = random.choice(all_options[poll])
                Vote.objects.create(
                    user=user,
                    poll=poll,
                    option=option,
                )

        print("[Ingress] Demo polls created successfully.")

    # ---------------------------------------------------------
    # HELPER: Create poll with unique slug
    # ---------------------------------------------------------
    def create_poll(self, name, question, active=True):
        base_slug = slugify(name)
        slug = base_slug
        counter = 1

        # Ensure slug uniqueness
        while Poll.objects.filter(slug=slug).exists():
            counter += 1
            slug = f"{base_slug}-{counter}"

        poll = Poll.objects.create(
            name=name,
            slug=slug,
            question_text=question,
            pub_date=timezone.now(),
            is_active=active,
        )

        print(f"[Ingress] Created poll: {name} (slug={slug})")
        return poll
