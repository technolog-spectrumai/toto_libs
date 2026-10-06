"""What stage 64's test modules share: two communities with their people,
and the short ways to make a pin, a zone and a comment through the doors."""

from __future__ import annotations

import io

from django.apps import apps
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import CommunityPin, CommunityZone, GeographyUsageEvent
from .testing import SQUARE, Economy, client_of, community, fresh_cache, member, op, post

PIN = {"lat": 52.2297, "lng": 21.0122, "name": "Well", "postal_address": "Rynek 1",
       "note": "open on Sundays"}
AREA = {"name": "Meadow", "description": "between the river and the road", "outline": SQUARE}


class LocationsCase(TestCase):
    """Guild: hugo is its head, mia a member, sen a senior member. Other:
    olga is its head and belongs to nothing else. stef is staff and belongs
    to nothing. root is a superuser on the Superuser plan."""

    billed = False

    def setUp(self):
        from toto.core.models import Platform

        fresh_cache(self)
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.economy = Economy(self) if self.billed else None
        self.head_user, self.head = member("hugo")
        self.member_user, self.member = member("mia")
        self.senior_user, self.senior = member("sen")
        self.other_user, self.other_head = member("olga")
        self.staff_user, _staff = member("stef", is_staff=True)
        self.guild = community("Guild", head=self.head)
        self.other = community("Other", head=self.other_head)
        self.member.communities.add(self.guild)
        self.guild.senior_members.add(self.senior)
        self.root = type(self.head_user).objects.create_superuser("root", "r@example.test", "pw")
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())
            self.root = type(self.root).objects.get(pk=self.root.pk)
        self.page_url = reverse("geography:locations")

    # -- addresses -------------------------------------------------------

    def url(self, name, row=None, community=None, **more):
        kwargs = dict(more)
        if row is not None:
            kwargs["uid"] = row.uid
            if name.endswith(("_detail", "_delete", "_hide", "_restore")) \
                    and not name.startswith("my_"):
                kwargs["slug"] = (community or row.community).slug
        elif community is not None:
            kwargs["slug"] = community.slug
        return reverse(f"geography:{name}", kwargs=kwargs)

    # -- making things through the doors -----------------------------------

    def save_pin(self, user=None, community=None, **changes):
        return post(client_of(user or self.member_user),
                    self.url("pin_create", community=community or self.guild),
                    {**PIN, "op": op(), **changes})

    def save_zone(self, user=None, community=None, **changes):
        return post(client_of(user or self.member_user),
                    self.url("zone_create", community=community or self.guild),
                    {**AREA, "op": op(), **changes})

    def pin(self, user=None, community=None, **changes) -> CommunityPin:
        response = self.save_pin(user, community, **changes)
        self.assertEqual(response.status_code, 200, response.content)
        return CommunityPin.objects.get(uid=response.json()["pin"]["uid"])

    def zone(self, user=None, community=None, **changes) -> CommunityZone:
        response = self.save_zone(user, community, **changes)
        self.assertEqual(response.status_code, 200, response.content)
        return CommunityZone.objects.get(uid=response.json()["zone"]["uid"])

    def comment(self, user, row, body="Seen it", name="pin_comment_add", **more):
        return client_of(user).post(self.url(name, row),
                                    {"body": body, "op": op(), **more})

    def events(self, metric=None):
        rows = GeographyUsageEvent.objects.all()
        return rows.filter(metric_code=metric) if metric else rows
