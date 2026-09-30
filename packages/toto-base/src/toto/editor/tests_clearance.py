"""The ACE editor's own doors follow the bucket's clearances (2026-09-30).

Open, save, delete and the sync socket used to fetch by `owner=` alone, so an
owner who lacks their bucket's clearance still opened and saved there — against
"no owner bypass". They ask `access.gate_by_bucket` first now: a file hidden by
its bucket is missing (404, a socket turned away), to its owner too.

`toto` is a namespace package: run as `manage.py test toto.editor.tests_clearance`."""

import json

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from django.urls import reverse

from toto.editor.routing import websocket_urlpatterns
from toto.editor.tests import EditorTestCase
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault.models import BucketClearance, VaultFile

IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


class _Kept(EditorTestCase):
    def setUp(self):
        super().setUp()
        self.clearance = Clearance.objects.create(name="payroll", slug="payroll")
        BucketClearance.objects.create(bucket=self.bucket, clearance=self.clearance)

    def hold(self, user):
        person, _ = Person.objects.get_or_create(user=user,
                                                 defaults={"display_name": user.username})
        person.clearances.add(self.clearance)

    def page(self):
        return self.client.get(reverse("editor:text_display", args=[self.file.pk]))

    def save(self, content):
        return self.client.post(reverse("editor:text_save", args=[self.file.pk]),
                                {"content": content})

    def delete(self):
        return self.client.post(reverse("editor:text_delete", args=[self.file.pk]))


class KeptBucketDoorTests(_Kept):
    def test_an_owner_without_the_clearance_cannot_open_the_file(self):
        self.assertEqual(self.page().status_code, 404)

    def test_an_owner_without_the_clearance_cannot_save_it(self):
        self.assertEqual(self.save("mine anyway\n").status_code, 404)
        self.assertEqual(self._on_disk(), "first\n")

    def test_an_owner_without_the_clearance_cannot_delete_it(self):
        self.assertEqual(self.delete().status_code, 404)
        self.assertTrue(VaultFile.objects.filter(pk=self.file.pk).exists())

    def test_an_owner_holding_the_clearance_opens_and_saves(self):
        self.hold(self.owner)
        self.assertContains(self.page(), "first")
        self.assertEqual(self.save("second\n").status_code, 200)
        self.assertEqual(self._on_disk(), "second\n")

    def test_an_owner_holding_the_clearance_deletes(self):
        self.hold(self.owner)
        self.assertEqual(self.delete().status_code, 200)
        self.assertFalse(VaultFile.objects.filter(pk=self.file.pk).exists())

    def test_the_clearance_opens_nothing_to_a_colleague(self):
        # The gate narrows the owner's door; it never widens it to a holder.
        self.hold(self.other)
        self.client.force_login(self.other)
        self.assertEqual(self.page().status_code, 404)
        self.assertEqual(self.save("theirs\n").status_code, 404)
        self.assertEqual(self._on_disk(), "first\n")


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class KeptBucketSocketTests(_Kept):
    def socket(self):
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns), f"/ws/editor/file/{self.file.pk}/")
        communicator.scope["user"] = self.owner
        return communicator

    def admitted(self):
        async def talk():
            communicator = self.socket()
            connected, _ = await communicator.connect(timeout=5)
            await communicator.disconnect()
            return connected

        return async_to_sync(talk)()

    def test_an_owner_without_the_clearance_is_turned_away(self):
        self.assertFalse(self.admitted())

    def test_an_owner_holding_the_clearance_is_admitted_and_writes(self):
        self.hold(self.owner)

        async def talk():
            communicator = self.socket()
            connected, _ = await communicator.connect(timeout=5)
            self.assertTrue(connected)
            await communicator.send_to(text_data=json.dumps({"content": "synced\n"}))
            answer = json.loads(await communicator.receive_from(timeout=5))
            await communicator.disconnect()
            return answer

        self.assertEqual(async_to_sync(talk)()["type"], "hash")
        self.assertEqual(self._on_disk(), "synced\n")

    def test_a_clearance_taken_away_closes_the_open_socket_unwritten(self):
        self.hold(self.owner)

        async def talk():
            communicator = self.socket()
            connected, _ = await communicator.connect(timeout=5)
            self.assertTrue(connected)
            await database_sync_to_async(
                self.owner.community_profile.clearances.remove)(self.clearance)
            await communicator.send_to(text_data=json.dumps({"content": "after\n"}))
            closed = await communicator.receive_output(timeout=5)
            await communicator.wait()
            return closed

        self.assertEqual(async_to_sync(talk)()["type"], "websocket.close")
        self.assertEqual(self._on_disk(), "first\n")
