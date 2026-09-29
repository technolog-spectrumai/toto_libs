"""Communities and circles from the console (2026-09-29).

    manage.py community_members list
    manage.py community_members join USERNAME --community devs --circle seniors

CONSOLE ONLY (``tools/create_user.py`` → ``deploy.py <config> communities`` /
``join``): a superuser's act at the server's shell, never a URL. ``list``
names every functional community and every circle; ``join`` puts an
existing account into some of each — making its Person if it has none, the
way accepting a reference does — through ``Person.communities``, so the
socialhub's audit records every membership. Functional communities and
circles are named separately on purpose: they are orthogonal (README), and a
typo must not quietly land somebody in the wrong kind.

One JSON line on stdout; exit 1 on a refusal, with nothing changed.
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from toto.people.models import Person
from toto.socialhub.models import Community


def listing() -> dict:
    def rows(qs):
        return [{"slug": c.slug, "name": c.name, "members": c.n}
                for c in qs.annotate(n=Count("members")).order_by("name")]

    return {"communities": rows(Community.objects.functional()),
            "circles": rows(Community.objects.circles())}


class Command(BaseCommand):
    help = "List communities and circles, or put an account into some. Console only."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("list", "join"))
        parser.add_argument("username", nargs="?", default="")
        parser.add_argument("--community", action="append", default=[],
                            help="a functional community's slug (repeatable)")
        parser.add_argument("--circle", action="append", default=[],
                            help="a circle's slug (repeatable)")

    def emit(self, payload: dict) -> None:
        self.stdout.write(json.dumps(payload, sort_keys=True))

    def refuse(self, message: str) -> None:
        self.emit({"ok": False, "error": message})
        raise SystemExit(1)

    def handle(self, *args, **options):
        if options["action"] == "list":
            self.emit({"ok": True, **listing()})
            return
        User = get_user_model()
        username = options["username"]
        if not username:
            self.refuse("join needs a username")
        user = User.objects.filter(**{User.USERNAME_FIELD: username}).first()
        if user is None:
            self.refuse(f"there is no account called {username!r}")
        functional = {c.slug: c for c in Community.objects.functional()
                      .filter(slug__in=options["community"])}
        circles = {c.slug: c for c in Community.objects.circles().filter(slug__in=options["circle"])}
        unknown = ([f"community {s!r}" for s in options["community"] if s not in functional]
                   + [f"circle {s!r}" for s in options["circle"] if s not in circles])
        if unknown:
            self.refuse("no such " + ", ".join(unknown)
                        + " — `deploy.py <config> communities` lists them")
        with transaction.atomic():
            person, made = Person.objects.get_or_create(
                user=user, defaults={"display_name": user.get_full_name() or user.get_username(),
                                     "email": user.email or ""})
            person.communities.add(*functional.values(), *circles.values())
        self.emit({"ok": True, "username": username, "person_created": made,
                   "communities": sorted(functional), "circles": sorted(circles)})
