"""What geography's test modules share: people, communities, an op, and a
ledger that can be told to refuse."""

from __future__ import annotations

import json
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client

User = get_user_model()


def op() -> str:
    return str(uuid.uuid4())


def member(username, **flags):
    """A user with a person: ``(user, person)``."""
    from toto.people.models import Person

    user = User.objects.create_user(username, password="pw", email=f"{username}@example.test",
                                    **flags)
    person = Person.objects.filter(user=user).first()
    if person is None:
        person = Person.objects.create(user=user, display_name=username.title(),
                                       slug=username)
    return user, person


def community(name, head=None):
    from toto.socialhub.models import Community

    made = Community.objects.create(name=name, head=head)
    return made


def client_of(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def refusing_ledger(target="toto.geography.charging.charge"):
    """The ledger refuses at the charge, after the work."""
    from toto.quota.charge import InsufficientFunds

    return mock.patch(target, side_effect=_funds_error(InsufficientFunds))


def no_funds(target="toto.geography.charging.check_funds"):
    """The afford check refuses, before the work."""
    from toto.quota.charge import InsufficientFunds

    return mock.patch(target, side_effect=_funds_error(InsufficientFunds))


def _funds_error(cls):
    try:
        return cls("RED", 1, 0)
    except TypeError:
        return cls()


def fresh_cache(test):
    cache.clear()
    test.addCleanup(cache.clear)


SQUARE = [[52.0, 21.0], [52.0, 21.1], [52.1, 21.1], [52.1, 21.0]]
BOWTIE = [[52.0, 21.0], [52.1, 21.1], [52.0, 21.1], [52.1, 21.0]]


class Economy:
    """The host's real ledger, where there is one: every metric at its seed
    price, the member's three pools filled. ``Economy(test)`` skips the test
    on a host that bills nothing."""

    def __init__(self, test):
        from django.apps import apps
        from django.test import override_settings

        if not (apps.is_installed("toto.tariffs") and apps.is_installed("toto.mana")):
            test.skipTest("this host bills nothing")
        from toto.mana.tests import fixtures

        self.fixtures = fixtures
        master = override_settings(**fixtures.MASTER)
        master.enable()
        test.addCleanup(master.disable)
        fixtures.economy()
        fixtures.seed_prices()

    def held(self, user, role):
        return self.fixtures.held(user, role)

    def empty(self, user, role, leave="0.1"):
        from decimal import Decimal

        self.fixtures.spend(user, role, self.fixtures.held(user, role) - Decimal(leave))

    @staticmethod
    def charges(metric=None) -> int:
        """How many charges the rate card has posted (its usage records)."""
        from toto.tariffs.models import UsageRecord

        rows = UsageRecord.objects.all()
        if metric is not None:
            rows = rows.filter(metric_code=metric)
        return rows.count()
