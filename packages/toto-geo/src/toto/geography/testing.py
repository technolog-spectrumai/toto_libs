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


# ---------------------------------------------------------------------------
# A page's HTML as a tree, for the tests that ask where a control is
# ---------------------------------------------------------------------------

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}


def tree_of(html: str) -> list:
    """The elements of ``html`` as nested dicts: ``{"tag", "attrs",
    "children"}``, a text as ``{"text"}``. An attribute with no value reads
    ``""``. Far enough for a page this app draws; no browser's repairs."""
    from html.parser import HTMLParser

    root = {"tag": "", "attrs": {}, "children": []}
    stack = [root]

    class Build(HTMLParser):
        def handle_starttag(self, tag, attrs):
            node = {"tag": tag, "attrs": {name: "" if value is None else value
                                          for name, value in attrs}, "children": []}
            stack[-1]["children"].append(node)
            if tag not in _VOID:
                stack.append(node)

        def handle_startendtag(self, tag, attrs):
            stack[-1]["children"].append(
                {"tag": tag, "attrs": {name: "" if value is None else value
                                       for name, value in attrs}, "children": []})

        def handle_endtag(self, tag):
            for index in range(len(stack) - 1, 0, -1):
                if stack[index]["tag"] == tag:
                    del stack[index:]
                    break

        def handle_data(self, data):
            if data.strip():
                stack[-1]["children"].append({"text": data})

    parser = Build(convert_charrefs=True)
    parser.feed(html)
    parser.close()
    return root["children"]


def walk(tree, above=()):
    """Every element of ``tree`` with the elements it is inside, outermost
    first: ``(node, ancestors)``."""
    for node in tree:
        if "tag" not in node:
            continue
        yield node, above
        yield from walk(node["children"], above + (node,))


def element(tree, attr, value=None):
    """The first element that has ``attr`` (with ``value``, when one is
    given), or None."""
    for node, _above in walk(tree):
        if attr in node["attrs"] and (value is None or node["attrs"][attr] == value):
            return node
    return None


def holds(node, attr, value=None) -> bool:
    """Is an element with ``attr`` (and ``value``) inside ``node``?"""
    return element(node["children"], attr, value) is not None


def _words_field(node) -> bool:
    """A control a point's or a zone's words are typed or chosen in: a text
    input, a textarea, or the choice of the one community it belongs to.
    Not a search box, a checkbox, or a select of the page's own tools."""
    tag, attrs = node["tag"], node["attrs"]
    if tag == "textarea":
        return True
    if tag == "input":
        return attrs.get("type", "text") == "text"
    if tag == "select":
        return "community" in (attrs.get("data-geo") or attrs.get("name") or "")
    return False


def words_fields(html: str, root_attr: str) -> tuple[list, list]:
    """``(outside, inside)``: the names of the words' controls of the map
    whose box carries ``root_attr``, those that are NOT in a
    ``role="dialog"`` and those that are. The owner, 2026-10-06: "the
    information like name etc should be inputed via modal and modal only"."""
    outside, inside = [], []
    for node, above in walk(tree_of(html)):
        if not any(root_attr in up["attrs"] for up in above) or not _words_field(node):
            continue
        name = node["attrs"].get("data-geo") or node["attrs"].get("name") or node["tag"]
        in_dialog = any(up["attrs"].get("role") == "dialog" for up in above)
        (inside if in_dialog else outside).append(name)
    return outside, inside


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
