"""The one safe way onto the Superuser plan (2026-09-26). Idempotent.

A superuser is put on the admin-only plan by nobody: `plan_for` no longer
hands it out for the privilege alone, and `is_eligible` wants a Community
that offers it. So a fresh host has admins on Free until this runs — they
can still reach /admin/ and /plans/, which are always free, and the Current
plan page tells them to run this. The build scripts run it right after they
make the `admin` account.

What it does, every time it runs, changing only what is missing:

* the Community ``operators`` ("Operators", the platform's own), headed by
  the first superuser;
* an offer of EVERY plan in the ladder to it, the admin-only one included;
* every active superuser made a member (a Person is created for one who has
  none) and subscribed to the admin-only plan, when the ladder has one and
  the account is not on it already.

Nothing is taken away: a superuser already on some other plan keeps it (an
admin who chose Developer to test the ladder is not moved). Nothing here
touches an ordinary member.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from toto.subscriptions import plans, services
from toto.subscriptions.models import CommunityPlanOffer, Subscription

SLUG = "operators"
NAME = "Operators"


class Command(BaseCommand):
    help = "Make the operators' Community, offer every plan to it, put superusers on the admin plan."

    @transaction.atomic
    def handle(self, *args, **options):
        problems = plans.validate()
        if problems:
            raise CommandError("plans.yaml is not usable:\n  - " + "\n  - ".join(problems))
        from toto.people.models import Person
        from toto.socialhub.models import Community

        User = get_user_model()
        superusers = list(User.objects.filter(is_superuser=True, is_active=True).order_by("pk"))
        head = None
        if superusers:
            head, _ = Person.objects.get_or_create(
                user=superusers[0], defaults={"display_name": superusers[0].get_username()})
        community = Community.objects.filter(slug=SLUG).first()
        if community is None:
            community = Community.objects.create(name=NAME, slug=SLUG, head=head)
            self.stdout.write(self.style.SUCCESS(f"Created the '{NAME}' community."))
        elif community.head_id is None and head is not None:
            community.head = head
            community.save(update_fields=["head"])

        offered = 0
        for plan in plans.all_plans():
            _, new = CommunityPlanOffer.objects.get_or_create(community=community, plan_key=plan.key)
            offered += int(new)
        self.stdout.write(f"Offered every plan to '{NAME}' ({offered} new).")

        reserved = plans.admin_plan()
        placed = 0
        for user in superusers:
            person, _ = Person.objects.get_or_create(
                user=user, defaults={"display_name": user.get_username()})
            person.communities.add(community)
            if reserved is None:
                continue
            row = Subscription.objects.filter(user=user).first()
            if row is not None and row.is_paying and not row.is_expired():
                continue                      # keeps whatever plan they hold
            services.subscribe(user, reserved)
            placed += 1
        if reserved is None:
            self.stdout.write(self.style.WARNING(
                "The ladder has no admin-only plan; superusers were made members and nothing else."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"{len(superusers)} superuser(s) in '{NAME}', {placed} put on '{reserved.key}'."))
