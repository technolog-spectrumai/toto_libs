"""The live socket (2026-10-04): who may open it, what it hears, and that a
folder event never reaches somebody who may not read the file.

Through the stack a host builds (``same_origin_validator`` over channels'
``AuthMiddlewareStack`` over this app's route).

    manage.py test toto.notify.tests_consumer
"""

import tempfile

from asgiref.sync import sync_to_async
from channels.auth import AuthMiddlewareStack
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client, TransactionTestCase, override_settings

from toto.api.ws_origin import same_origin_validator
from toto.core import live
from toto.notify import consumers
from toto.notify.routing import websocket_urlpatterns
from toto.notify.testing import MEMORY_LAYER
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault.live import folder_group
from toto.vault.models import Bucket, BucketClearance, VaultDirectory, VaultFile

User = get_user_model()
HOST = "zenobia.example.org"
ORIGIN = b"http://" + HOST.encode()

application = same_origin_validator(AuthMiddlewareStack(URLRouter(websocket_urlpatterns)))


def cookie_for(user):
    client = Client()
    client.force_login(user)
    return client.cookies[settings.SESSION_COOKIE_NAME].value


@override_settings(ALLOWED_HOSTS=[HOST], CHANNEL_LAYERS=MEMORY_LAYER, CSRF_TRUSTED_ORIGINS=[],
                   MEDIA_ROOT=tempfile.mkdtemp(prefix="notify-consumer-"))
class SocketCase(TransactionTestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="pw")
        self.ada = User.objects.create_user("ada", password="pw")
        self.bucket = Bucket.objects.create(name="Work", slug="work", owner=self.owner)
        self.folder = VaultDirectory.objects.create(name="Papers", bucket=self.bucket,
                                                    owner=self.owner)

    def socket(self, user=None, *, origin=ORIGIN, path="/ws/live/", cookie=None):
        headers = [(b"host", HOST.encode())]
        if origin is not None:
            headers.append((b"origin", origin))
        cookie = cookie or (cookie_for(user) if user is not None else None)
        if cookie:
            headers.append((b"cookie", f"{settings.SESSION_COOKIE_NAME}={cookie}".encode()))
        return WebsocketCommunicator(application, path, headers=headers)

    def file(self, owner, *, public=False, folder="default"):
        vault_file = VaultFile(owner=owner, title="plan.txt", file_type="text",
                               key=f"k{VaultFile.all_objects.count()}", bucket=self.bucket,
                               directory=self.folder if folder == "default" else folder,
                               is_public=public)
        vault_file.file.save("plan.txt", ContentFile(b"x"), save=False)
        vault_file.save()
        return vault_file


class HandshakeTests(SocketCase):
    async def test_a_signed_in_session_from_the_platforms_own_origin_opens_it(self):
        socket = self.socket(cookie=await sync_to_async(cookie_for)(self.ada))
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        await socket.disconnect()

    async def test_an_anonymous_handshake_is_closed(self):
        self.assertEqual(await self.socket().connect(), (False, consumers.CLOSE_NOT_SIGNED_IN))
        self.assertEqual((await self.socket(cookie="x" * 32).connect())[0], False)

    async def test_another_origin_is_refused_and_so_is_none_at_all(self):
        cookie = await sync_to_async(cookie_for)(self.ada)
        for origin in (b"https://evil.example.com", b"http://zenobia.example.org:8081",
                       b"https://zenobia.example.org", b"null", None,
                       b"http://zenobia.example.org.evil.com"):
            with self.subTest(origin=origin):
                connected, _ = await self.socket(cookie=cookie, origin=origin).connect()
                self.assertFalse(connected)

    async def test_a_trusted_origin_is_let_in(self):
        cookie = await sync_to_async(cookie_for)(self.ada)
        with override_settings(CSRF_TRUSTED_ORIGINS=["https://zenobia.example.org"]):
            connected, _ = await self.socket(cookie=cookie,
                                             origin=b"https://zenobia.example.org").connect()
        self.assertTrue(connected)

    async def test_there_is_no_token_door_and_no_other_route(self):
        cookie = await sync_to_async(cookie_for)(self.ada)
        connected, _ = await self.socket(path=f"/ws/live/?token={cookie}").connect()
        self.assertFalse(connected)
        bearer = WebsocketCommunicator(application, "/ws/live/", subprotocols=["toto.bearer", cookie],
                                       headers=[(b"host", HOST.encode()), (b"origin", ORIGIN)])
        self.assertFalse((await bearer.connect())[0])
        with self.assertRaises(ValueError):
            await self.socket(cookie=cookie, path="/ws/forum/hall/").connect()


class MessageTests(SocketCase):
    async def open(self, user):
        socket = self.socket(cookie=await sync_to_async(cookie_for)(user))
        self.assertTrue((await socket.connect())[0])
        return socket

    async def test_a_notification_is_a_poke_with_nothing_in_it(self):
        socket = await self.open(self.ada)
        other = await self.open(self.owner)
        await get_channel_layer().group_send(live.user_group(self.ada.pk),
                                             {"type": live.NOTIFICATION, "id": 5, "text": "x"})
        self.assertEqual(await socket.receive_json_from(), {"type": "notification"})
        self.assertTrue(await other.receive_nothing(0.1))
        await socket.disconnect()
        await other.disconnect()

    async def test_anything_but_a_watch_closes_the_socket(self):
        for frame in ('{"type": "read", "id": 1}', "not json", '{"type": "watch"}',
                      '{"type": "watch", "directory": "1"}', '{"type": "watch", "directory": true}',
                      '{"type": "watch", "directory": -1}', "x" * 500, "[]"):
            with self.subTest(frame=frame[:30]):
                socket = await self.open(self.ada)
                await socket.send_to(text_data=frame)
                closed = await socket.receive_output()
                self.assertEqual((closed["type"], closed["code"]),
                                 ("websocket.close", consumers.CLOSE_BAD_MESSAGE))
        socket = await self.open(self.ada)
        await socket.send_to(bytes_data=b"\x00")
        self.assertEqual((await socket.receive_output())["code"], consumers.CLOSE_BAD_MESSAGE)

    async def watch(self, socket, directory_id):
        await socket.send_json_to({"type": "watch", "directory": directory_id})
        return await socket.receive_json_from()

    async def test_a_folder_one_may_read_is_watched(self):
        socket = await self.open(self.ada)
        self.assertEqual(await self.watch(socket, self.folder.pk),
                         {"type": "watching", "directory": self.folder.pk, "ok": True})
        await socket.disconnect()

    async def test_a_folder_one_cannot_read_and_one_that_is_not_there_answer_the_same(self):
        locked = await sync_to_async(VaultDirectory.objects.create)(
            name="Board", bucket=self.bucket, owner=self.owner)
        await sync_to_async(locked.allowed_users.add)(self.owner)
        socket = await self.open(self.ada)
        for directory_id in (locked.pk, 987654):
            answer = await self.watch(socket, directory_id)
            self.assertEqual(answer, {"type": "watching", "directory": directory_id, "ok": False})
        await get_channel_layer().group_send(folder_group(locked.pk), {
            "type": live.FOLDER, "kind": "added", "file": 1, "directory": locked.pk})
        self.assertTrue(await socket.receive_nothing(0.1))
        await socket.disconnect()

    async def test_a_kept_bucket_refuses_the_watch_even_to_its_owner(self):
        def keep():
            BucketClearance.objects.create(bucket=self.bucket,
                                           clearance=Clearance.objects.create(name="Payroll"))
        await sync_to_async(keep)()
        socket = await self.open(self.owner)
        self.assertFalse((await self.watch(socket, self.folder.pk))["ok"])
        await socket.disconnect()

    async def test_an_event_reaches_only_a_watcher_who_may_read_the_file(self):
        """Watching a folder is not reading every file in it."""
        reader, stranger = await self.open(self.owner), await self.open(self.ada)
        for socket in (reader, stranger):
            self.assertTrue((await self.watch(socket, self.folder.pk))["ok"])
        private = await sync_to_async(self.file)(self.owner)
        said = await reader.receive_json_from()
        self.assertEqual(said, {"type": "folder", "kind": "added", "file": private.pk,
                                "directory": self.folder.pk})
        self.assertNotIn("plan", str(said))
        self.assertTrue(await stranger.receive_nothing(0.2))
        public = await sync_to_async(self.file)(self.owner, public=True)
        self.assertEqual((await stranger.receive_json_from())["file"], public.pk)
        await reader.receive_json_from()
        # Trashed: told to the one who could read it there, with no reader fact.
        await sync_to_async(lambda: VaultFile.objects.get(pk=private.pk).trash(by=self.owner))()
        gone = await reader.receive_json_from()
        self.assertEqual(gone, {"type": "folder", "kind": "removed", "file": private.pk,
                                "directory": self.folder.pk})
        self.assertTrue(await stranger.receive_nothing(0.2))
        await reader.disconnect()
        await stranger.disconnect()

    async def test_a_bucket_hidden_after_the_watch_began_leaks_no_event(self):
        socket = await self.open(self.ada)
        self.assertTrue((await self.watch(socket, self.folder.pk))["ok"])

        def keep_and_upload():
            BucketClearance.objects.create(bucket=self.bucket,
                                           clearance=Clearance.objects.create(name="Payroll"))
            return self.file(self.owner, public=True)
        await sync_to_async(keep_and_upload)()
        self.assertTrue(await socket.receive_nothing(0.2))
        await socket.disconnect()

    async def test_a_session_that_ended_is_told_nothing_more(self):
        cookie = await sync_to_async(cookie_for)(self.ada)
        socket = self.socket(cookie=cookie)
        self.assertTrue((await socket.connect())[0])
        from django.contrib.sessions.backends.db import SessionStore

        await sync_to_async(SessionStore(session_key=cookie).delete)()
        await get_channel_layer().group_send(live.user_group(self.ada.pk),
                                             {"type": live.NOTIFICATION})
        closed = await socket.receive_output()
        self.assertEqual((closed["type"], closed["code"]),
                         ("websocket.close", consumers.CLOSE_NOT_SIGNED_IN))

    async def test_a_changed_password_and_a_deactivated_account_end_it_too(self):
        def change():
            user = User.objects.get(pk=self.ada.pk)
            user.set_password("another")
            user.save()
        cookie = await sync_to_async(cookie_for)(self.ada)
        self.assertIsNotNone(await sync_to_async(consumers.signed_in_user)(cookie, self.ada.pk))
        self.assertIsNone(await sync_to_async(consumers.signed_in_user)(cookie, self.owner.pk))
        await sync_to_async(change)()
        self.assertIsNone(await sync_to_async(consumers.signed_in_user)(cookie, self.ada.pk))
        cookie = await sync_to_async(cookie_for)(self.owner)
        await sync_to_async(User.objects.filter(pk=self.owner.pk).update)(is_active=False)
        self.assertIsNone(await sync_to_async(consumers.signed_in_user)(cookie, self.owner.pk))

    async def test_presence_is_forwarded_as_a_toast(self):
        socket = await self.open(self.ada)
        await get_channel_layer().group_send(live.user_group(self.ada.pk), {
            "type": live.PRESENCE, "event": "in", "name": "Bob", "link": "/socialhub/profiles/bob/"})
        self.assertEqual(await socket.receive_json_from(), {
            "type": "presence", "event": "in", "name": "Bob", "link": "/socialhub/profiles/bob/"})
        await socket.disconnect()
