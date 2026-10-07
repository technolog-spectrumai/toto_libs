"""What the forum's test modules share: the secret, people on plans, a
community with its channel, and small pictures."""

from __future__ import annotations

import json
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from . import channels, keys

User = get_user_model()

SECRET = "forum-test-secret-not-a-real-one"

#: The smallest files that begin as each type does.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24 + b"picture-of-the-harbour"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"jpeg-bytes"
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8


def op() -> str:
    return str(uuid.uuid4())


def member(username, **flags):
    """A user with a person: ``(user, person)``."""
    from toto.people.models import Person

    user = User.objects.create_user(username, password="pw", email=f"{username}@example.test",
                                    **flags)
    person = Person.objects.filter(user=user).first()
    if person is None:
        person = Person.objects.create(user=user, display_name=username.title(), slug=username)
    return user, person


def on_plan(user, key="professional"):
    """Put ``user`` on a plan of the host's ladder (``professional`` carries
    the forum; ``admin`` is the admin-only plan)."""
    from toto.subscriptions import plans, services

    plan = plans.admin_plan() if key == "admin" else plans.plan(key)
    services.subscribe(user, plan, force=True)
    return user


def client_of(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def send_json(client, url, payload=None):
    return client.post(url, data=json.dumps(payload or {}), content_type="application/json")


def upload(data=PNG, name="harbour.png", content_type="image/png"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def restart() -> None:
    """Forget what this process holds of the keys: the next read opens the
    wrapped rows again with the secret, as a fresh process would."""
    keys.forget()
    keys.vault.clear_cache()


@override_settings(FORUM_VAULT_PASSWORD=SECRET)
class ForumCase(TestCase):
    """One community, the Guild, with its channel, and one visitor of every
    kind the access rule tells apart."""

    @classmethod
    def setUpClass(cls):
        restart()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        restart()

    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform
        from toto.socialhub.models import Community

        Platform.objects.get_or_create(
            site_name="T", defaults={"author": "A", "publication_year": 2026, "active": True})
        cls.head, cls.head_person = member("head")
        cls.guild = Community.objects.create(name="Guild", head=cls.head_person)
        cls.other = Community.objects.create(name="Other")
        cls.member, cls.member_person = member("mem")
        cls.member_person.communities.add(cls.guild)
        cls.second, cls.second_person = member("second")
        cls.second_person.communities.add(cls.guild)
        cls.senior, cls.senior_person = member("senior")
        cls.guild.senior_members.add(cls.senior_person)
        cls.outsider, cls.outsider_person = member("outsider")
        cls.outsider_person.communities.add(cls.other)
        cls.free, cls.free_person = member("free")
        cls.free_person.communities.add(cls.guild)
        cls.staff, _person = member("staff", is_staff=True)
        cls.admin, _person = member("admin", is_superuser=True, is_staff=True)
        for user in (cls.head, cls.member, cls.second, cls.senior, cls.outsider, cls.staff):
            on_plan(user)
        on_plan(cls.admin, "admin")
        cls.channel = channels.ensure_channel(cls.guild)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    # -- addresses ----------------------------------------------------------

    def url(self, name, *args, community=None):
        return reverse(f"forum:{name}", args=[(community or self.guild).slug, *args])

    # -- doing things -------------------------------------------------------

    def say(self, user, text="hello", image=None, the_op=None, community=None):
        data = {"op": the_op or op(), "text": text}
        if image is not None:
            data["image"] = image
        return client_of(user).post(self.url("post", community=community), data)

    def feed(self, user, **query):
        return client_of(user).get(self.url("feed"), query)

    def open_poll(self, user, title="Lunch?", options="Soup\nSalad: with bread", **more):
        return send_json(client_of(user), self.url("poll_open"),
                         {"title": title, "options": options, **more})
