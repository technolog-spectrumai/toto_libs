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


class ProviderAnswer:
    """An outside service's answer as ``urlopen`` hands it over, read in
    pieces as ``provider.read_capped`` reads it, or whole. ``reads`` counts
    the reads; ``piece`` is the most one read gives (a slow service gives
    little at a time)."""

    def __init__(self, body: bytes, piece: int | None = None):
        self.body, self.at, self.reads, self.piece = body, 0, 0, piece

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read1(self, size=-1):
        self.reads += 1
        left = len(self.body) - self.at
        take = left if size is None or size < 0 else min(left, size)
        if self.piece is not None:
            take = min(take, self.piece)
        chunk = self.body[self.at:self.at + take]
        self.at += take
        return chunk

    read = read1


def provider_answering(target, payload, piece=None):
    """Patch ``urlopen`` at ``target`` to answer ``payload`` (bytes, or
    anything JSON holds), a fresh answer for every call."""
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return mock.patch(target, side_effect=lambda *args, **kwargs: ProviderAnswer(body, piece))


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
