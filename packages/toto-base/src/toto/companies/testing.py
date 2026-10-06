"""What the companies test modules share: a platform, people of every
standing, two companies and an ordinary community."""

from __future__ import annotations

import io

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

User = get_user_model()

#: What a refused visitor gets: the door's own 403, or a host's sign-in
#: gate's redirect before the door is reached.
SIGNED_OUT = (302, 403)


def member(username, name=None, **flags):
    """A user with a person: ``(user, person)``."""
    from toto.people.models import Person

    user = User.objects.create_user(username, password="pw",
                                    email=f"{username}@example.test", **flags)
    person = Person.objects.filter(user=user).first()
    if person is None:
        person = Person.objects.create(user=user, display_name=name or username.title(),
                                       slug=username)
    return user, person


def community(name, head=None, org_type="company"):
    from toto.socialhub.models import Community

    return Community.objects.create(name=name, head=head, org_type=org_type)


def client_of(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def page_url(company, tab="") -> str:
    url = reverse("socialhub:community_detail", args=[company.slug])
    return f"{url}?tab={tab}" if tab else url


def audit_records(prefix="COMPANIES."):
    """The audit records this app wrote, oldest first (none on a host
    without the chain)."""
    if not apps.is_installed("toto.audit"):
        return []
    from toto.audit.models import AuditRecord

    return list(AuditRecord.objects.filter(action__startswith=prefix).order_by("sequence"))


class CompaniesTestCase(TestCase):
    """Acme and Globex are companies, the Guild is an ordinary community.

    ``head`` heads Acme and the Guild, ``other_head`` Globex. ``mia`` is a
    member of Acme, ``sen`` a senior member of it, ``stan`` belongs nowhere,
    ``hold`` holds shares in Acme and is no member, ``stef`` is staff and
    nothing else, ``root`` an administrator (a superuser on the Superuser
    plan where the host sells it), ``late`` a superuser made after the plans
    were handed out.
    """

    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "A", "publication_year": 2026})
        cls.head_user, cls.head = member("hugo")
        cls.other_head_user, cls.other_head = member("olga")
        cls.mia_user, cls.mia = member("mia")
        cls.sen_user, cls.sen = member("sen")
        cls.stan_user, cls.stan = member("stan")
        cls.hold_user, cls.hold = member("hold")
        cls.stef_user, cls.stef = member("stef", is_staff=True)
        bare, cls.root = member("root", is_superuser=True, is_staff=True)
        cls.acme = community("Acme", head=cls.head)
        cls.globex = community("Globex", head=cls.other_head)
        cls.guild = community("Guild", head=cls.head, org_type="guild")
        cls.mia.communities.add(cls.acme)
        cls.acme.senior_members.add(cls.sen)
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())   # root: the Superuser plan
        cls.root_user = User.objects.get(pk=bare.pk)
        cls.late_user, cls.late = member("late", is_superuser=True, is_staff=True)

    # -- the doors ---------------------------------------------------------

    @staticmethod
    def number_url(company):
        return reverse("companies:number", args=[company.slug])

    @staticmethod
    def holding_url(company):
        return reverse("companies:holding_save", args=[company.slug])

    @staticmethod
    def delete_url(company, pk):
        return reverse("companies:holding_delete", args=[company.slug, pk])

    def record(self, company, person, quantity, user=None):
        """Post a holding as ``user`` (the company's head by default)."""
        return client_of(user or self.head_user).post(
            self.holding_url(company), {"person": person.slug, "quantity": str(quantity)})

    @staticmethod
    def hold_shares(company, person, quantity):
        """A holding written straight into the register."""
        from toto.companies.models import ShareHolding

        return ShareHolding.objects.create(community=company, person=person,
                                           quantity=quantity)
