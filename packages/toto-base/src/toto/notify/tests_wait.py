"""The long-poll door (2026-10-04): it answers at once when there is news,
holds when there is none, wakes on a change, and tells nobody of anything
that is not theirs.

    manage.py test toto.notify.tests_wait

The held requests are asked of the view itself, with an ASGI request (a
request with a scope is one the door may hold); the door's own refusals and
the WSGI answer go through the test client.
"""

import asyncio
import json
import tempfile
import time
from unittest import mock

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import AsyncRequestFactory, Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from toto import notify
from toto.core import live
from toto.notify import views, wait
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault import live as vault_live
from toto.vault.models import Bucket, BucketClearance, VaultDirectory, VaultFile

User = get_user_model()
HOLD = 0.5


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="notify-wait-"), LIVE_REDIS_URL="",
                   NOTIFY_WAIT_SECONDS=HOLD)
class Case(TestCase):
    def setUp(self):
        cache.clear()
        poll = mock.patch.object(live, "POLL_SECONDS", 0.05)
        poll.start()
        self.addCleanup(poll.stop)
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.bucket = Bucket.objects.create(name="Work", slug="work", owner=self.ada)
        self.folder = VaultDirectory.objects.create(name="Papers", bucket=self.bucket,
                                                    owner=self.ada)
        # Ada's alone: its access list names her and not Bob.
        self.closed = VaultDirectory.objects.create(name="Closed", bucket=self.bucket,
                                                    owner=self.ada)
        self.closed.allowed_users.add(self.ada)
        self.url = reverse("notify:api_wait")
        self.client.force_login(self.ada)

    _n = 0

    def file(self, title="secret-plan.txt", *, folder=None, public=True, owner=None):
        """A file put in a folder, its change published as a commit would."""
        type(self)._n += 1
        folder = folder or self.folder
        vault_file = VaultFile(owner=owner or self.ada, title=title, key=f"k{self._n}",
                               file_type="text", bucket=folder.bucket, is_public=public,
                               directory=folder)
        vault_file.file.save(title, ContentFile(b"x"), save=False)
        with self.captureOnCommitCallbacks(execute=True):
            vault_file.save()
        return vault_file

    def tell(self, user, title="salaries.csv", actor=None):
        with self.captureOnCommitCallbacks(execute=True):
            return notify.send(user, "vault.uploaded", actor=actor, title=title,
                               bucket=self.bucket.name, bucket_id=self.bucket.pk)

    async def ask(self, user, cursor="", folders=()):
        """The door, asked as an ASGI server would: ``(status, answer, seconds)``."""
        query = {}
        if cursor:
            query["cursor"] = cursor
        if folders:
            query["folders"] = ",".join(str(pk) for pk in folders)
        request = AsyncRequestFactory().get(self.url, query)
        request.user = user
        started = time.monotonic()
        response = await views.api_wait(request)
        return response.status_code, json.loads(response.content), time.monotonic() - started

    async def cursor_of(self, user, folders=()):
        status, first, _ = await self.ask(user, folders=folders)
        self.assertEqual(status, 200)
        return first["cursor"]


class AtOnceTests(Case):
    async def test_no_cursor_is_answered_at_once_with_one(self):
        status, data, seconds = await self.ask(self.ada)
        self.assertEqual(status, 200)
        self.assertEqual({k: v for k, v in data.items() if k != "cursor"},
                         {"notifications": False, "folders": []})
        self.assertTrue(data["cursor"])
        self.assertLess(seconds, HOLD)

    async def test_news_since_the_cursor_is_answered_at_once(self):
        cursor = await self.cursor_of(self.ada)
        await sync_to_async(self.tell)(self.ada, actor=self.bob)
        status, data, seconds = await self.ask(self.ada, cursor)
        self.assertEqual((status, data["notifications"], data["folders"]), (200, True, []))
        self.assertLess(seconds, HOLD)
        self.assertNotEqual(data["cursor"], cursor)

    async def test_a_folder_that_changed_since_the_cursor_is_answered_at_once(self):
        cursor = await self.cursor_of(self.ada, [self.folder.pk])
        vault_file = await sync_to_async(self.file)()
        status, data, seconds = await self.ask(self.ada, cursor, [self.folder.pk])
        self.assertLess(seconds, HOLD)
        self.assertEqual((data["notifications"], data["folders"]), (False, [self.folder.pk]))
        self.assertEqual(data["files"], {str(self.folder.pk): {
            str(vault_file.pk): vault_live.row_version(vault_file)}})

    async def test_a_folder_the_cursor_does_not_know_is_reported_once(self):
        cursor = await self.cursor_of(self.ada)
        _, data, seconds = await self.ask(self.ada, cursor, [self.folder.pk])
        self.assertLess(seconds, HOLD)
        self.assertEqual((data["folders"], data["files"]),
                         ([self.folder.pk], {str(self.folder.pk): {}}))
        _, again, seconds = await self.ask(self.ada, data["cursor"], [self.folder.pk])
        self.assertGreaterEqual(seconds, HOLD)
        self.assertEqual(again["folders"], [])


class HoldTests(Case):
    async def test_with_nothing_new_it_holds_then_answers_empty(self):
        cursor = await self.cursor_of(self.ada, [self.folder.pk])
        status, data, seconds = await self.ask(self.ada, cursor, [self.folder.pk])
        self.assertEqual(status, 200)
        self.assertGreaterEqual(seconds, HOLD)
        self.assertLess(seconds, HOLD + 5)
        self.assertEqual({k: v for k, v in data.items() if k != "cursor"},
                         {"notifications": False, "folders": []})
        self.assertEqual((wait.held(), live.waiting()), (0, 0))

    @override_settings(NOTIFY_WAIT_SECONDS=20)
    async def test_a_notification_wakes_it(self):
        cursor = await self.cursor_of(self.ada)
        held = asyncio.ensure_future(self.ask(self.ada, cursor))
        await asyncio.sleep(0.2)
        self.assertFalse(held.done())
        self.assertEqual(wait.held(self.ada.pk), 1)
        await sync_to_async(self.tell)(self.ada, actor=self.bob)
        status, data, seconds = await asyncio.wait_for(held, 10)
        self.assertEqual((status, data["notifications"], data["folders"]), (200, True, []))
        self.assertLess(seconds, 10)
        self.assertNotIn("salaries", json.dumps(data))
        self.assertEqual(set(data), {"cursor", "notifications", "folders"})

    @override_settings(NOTIFY_WAIT_SECONDS=20)
    async def test_a_file_in_a_watched_folder_wakes_it_with_ids_and_no_name(self):
        cursor = await self.cursor_of(self.ada, [self.folder.pk])
        held = asyncio.ensure_future(self.ask(self.ada, cursor, [self.folder.pk]))
        await asyncio.sleep(0.2)
        self.assertFalse(held.done())
        vault_file = await sync_to_async(self.file)("secret-plan.txt")
        _, data, seconds = await asyncio.wait_for(held, 10)
        self.assertLess(seconds, 10)
        self.assertEqual(data["folders"], [self.folder.pk])
        self.assertEqual(list(data["files"][str(self.folder.pk)]), [str(vault_file.pk)])
        self.assertNotIn("secret", json.dumps(data))
        self.assertEqual(set(data), {"cursor", "notifications", "folders", "files"})

    @override_settings(NOTIFY_WAIT_SECONDS=20)
    async def test_marking_read_in_one_tab_wakes_the_other(self):
        row = await sync_to_async(self.tell)(self.ada, actor=self.bob)
        cursor = await self.cursor_of(self.ada)
        held = asyncio.ensure_future(self.ask(self.ada, cursor))
        await asyncio.sleep(0.2)

        def read():
            with self.captureOnCommitCallbacks(execute=True):
                notify.services.mark_read(self.ada, row.pk)
        await sync_to_async(read)()
        _, data, _seconds = await asyncio.wait_for(held, 10)
        self.assertTrue(data["notifications"])

    @override_settings(NOTIFY_WAIT_SECONDS=20)
    async def test_what_another_process_published_is_found_by_looking_at_the_stamps(self):
        cursor = await self.cursor_of(self.ada, [self.folder.pk])
        held = asyncio.ensure_future(self.ask(self.ada, cursor, [self.folder.pk]))
        await asyncio.sleep(0.2)
        # Only the stamp, as a process without this one's waiters leaves it.
        cache.set(live.STAMP_PREFIX + vault_live.folder_key(self.folder.pk), "elsewhere", 60)
        _, data, seconds = await asyncio.wait_for(held, 10)
        self.assertLess(seconds, 10)
        self.assertEqual(data["folders"], [self.folder.pk])

    async def test_a_dropped_request_leaves_nothing_behind(self):
        cursor = await self.cursor_of(self.ada, [self.folder.pk])
        held = asyncio.ensure_future(self.ask(self.ada, cursor, [self.folder.pk]))
        await asyncio.sleep(0.2)
        self.assertEqual((wait.held(self.ada.pk), live.waiting()), (1, 2))
        held.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await held
        self.assertEqual((wait.held(), live.waiting()), (0, 0))


class NobodyElsesTests(Case):
    async def test_another_members_news_never_wakes_or_leaks(self):
        cursor = await self.cursor_of(self.ada, [self.folder.pk])
        held = asyncio.ensure_future(self.ask(self.ada, cursor, [self.folder.pk]))
        await asyncio.sleep(0.1)
        # Bob is told something, and a file lands in a folder Ada's page
        # does not watch: neither is Ada's business.
        await sync_to_async(self.tell)(self.bob, title="bobs-secret.txt", actor=self.ada)
        await sync_to_async(self.file)("elsewhere.txt", folder=self.closed)
        status, data, seconds = await held
        self.assertGreaterEqual(seconds, HOLD)
        self.assertEqual({k: v for k, v in data.items() if k != "cursor"},
                         {"notifications": False, "folders": []})
        self.assertNotIn("bob", json.dumps(data))
        # And Bob's own poll says his, not Ada's.
        bobs = await self.cursor_of(self.bob)
        await sync_to_async(self.tell)(self.ada, actor=self.bob)
        _, quiet, seconds = await self.ask(self.bob, bobs)
        self.assertGreaterEqual(seconds, HOLD)
        self.assertFalse(quiet["notifications"])

    async def test_a_cursor_of_another_account_is_no_cursor(self):
        adas = await self.cursor_of(self.ada, [self.folder.pk])
        self.assertIsNone(wait.read_cursor(self.bob, adas))
        self.assertIsNotNone(wait.read_cursor(self.ada, adas))
        for raw in ("", "x", adas[:-2] + "zz", "a" * 9000):
            self.assertIsNone(wait.read_cursor(self.ada, raw))
        _, data, seconds = await self.ask(self.bob, adas)
        self.assertLess(seconds, HOLD)              # as with none: where Bob stands
        self.assertEqual((data["notifications"], data["folders"]), (False, []))

    async def test_a_folder_the_reader_may_not_watch_is_never_reported(self):
        kept = await sync_to_async(self.kept_folder)()
        asked = [self.closed.pk, kept.pk, 999999]
        self.assertEqual(await sync_to_async(wait.watched)(self.bob, asked), [])
        _, first, seconds = await self.ask(self.bob, folders=asked)
        self.assertLess(seconds, HOLD)
        self.assertEqual(first["folders"], [])
        self.assertNotIn("files", first)
        # A file lands in each while Bob's request is held: it is not woken,
        # and its answer is the one of a poll that named no folder.
        held = asyncio.ensure_future(self.ask(self.bob, first["cursor"], asked))
        await asyncio.sleep(0.1)
        self.assertEqual(live.waiting(), 1)         # Bob's own bell, no folder
        await sync_to_async(self.file)("payroll.csv", folder=self.closed)
        await sync_to_async(self.file)("bonuses.csv", folder=kept)
        _, data, seconds = await held
        self.assertGreaterEqual(seconds, HOLD)
        self.assertEqual({k: v for k, v in data.items() if k != "cursor"},
                         {"notifications": False, "folders": []})
        _, plain, _seconds = await self.ask(self.bob, first["cursor"])
        self.assertEqual(wait.read_cursor(self.bob, data["cursor"]),
                         wait.read_cursor(self.bob, plain["cursor"]))

    def kept_folder(self):
        """A folder in a bucket kept to a clearance nobody here holds — its
        owner included (no owner bypass)."""
        bucket = Bucket.objects.create(name="Payroll", slug="payroll", owner=self.bob)
        BucketClearance.objects.create(bucket=bucket,
                                       clearance=Clearance.objects.create(name="Payroll"))
        Person.objects.get_or_create(user=self.bob, defaults={"display_name": "Bob"})
        return VaultDirectory.objects.create(name="Kept", bucket=bucket, owner=self.bob)

    async def test_the_owner_of_a_kept_bucket_is_not_told_either(self):
        kept = await sync_to_async(self.kept_folder)()
        _, data, _seconds = await self.ask(self.bob, folders=[kept.pk])
        self.assertEqual(data["folders"], [])

    @override_settings(NOTIFY_WAIT_SECONDS=1.5)
    async def test_access_taken_away_while_it_waits_is_obeyed_at_the_wake(self):
        cursor = await self.cursor_of(self.bob, [self.folder.pk])
        held = asyncio.ensure_future(self.ask(self.bob, cursor, [self.folder.pk]))
        await asyncio.sleep(0.2)
        # The folder gets an access list without Bob, then a file.
        await sync_to_async(self.folder.allowed_users.add)(self.ada)
        await sync_to_async(self.file)("after.txt")
        _, data, seconds = await held
        self.assertGreaterEqual(seconds, 1.5)
        self.assertEqual(data["folders"], [])
        self.assertNotIn("files", data)

    async def test_only_the_files_the_list_shows_the_reader_are_listed(self):
        public = await sync_to_async(self.file)("open.txt", public=True)
        private = await sync_to_async(self.file)("adas-own.txt", public=False)
        _, bobs, _seconds = await self.ask(self.bob, folders=[self.folder.pk])
        self.assertEqual(list(bobs["files"][str(self.folder.pk)]), [str(public.pk)])
        _, adas, _seconds = await self.ask(self.ada, folders=[self.folder.pk])
        self.assertEqual(sorted(adas["files"][str(self.folder.pk)]),
                         sorted([str(public.pk), str(private.pk)]))


@override_settings(NOTIFY_WAIT_SECONDS=20, NOTIFY_MAX_WAITS=3)
class CapTests(Case):
    async def test_a_fourth_held_request_sends_the_oldest_home(self):
        cursor = await self.cursor_of(self.ada)
        held = []
        for _ in range(3):
            held.append(asyncio.ensure_future(self.ask(self.ada, cursor)))
            await asyncio.sleep(0.1)
        self.assertEqual(wait.held(self.ada.pk), 3)
        self.assertFalse(any(task.done() for task in held))
        held.append(asyncio.ensure_future(self.ask(self.ada, cursor)))
        status, data, seconds = await asyncio.wait_for(held[0], 10)
        self.assertEqual(status, 200)
        self.assertLess(seconds, 10)
        self.assertEqual((data["notifications"], data["folders"]), (False, []))
        self.assertGreater(data["retry"], 0)
        self.assertEqual(wait.held(self.ada.pk), 3)
        self.assertFalse(any(task.done() for task in held[1:]))
        # Another account's requests are not counted with Ada's.
        bobs = asyncio.ensure_future(self.ask(self.bob, await self.cursor_of(self.bob)))
        await asyncio.sleep(0.1)
        self.assertEqual((wait.held(self.ada.pk), wait.held(self.bob.pk)), (3, 1))
        for task in [*held[1:], bobs]:
            task.cancel()
        await asyncio.gather(*held[1:], bobs, return_exceptions=True)
        self.assertEqual((wait.held(), live.waiting()), (0, 0))


class DoorTests(Case):
    """Through the test client: the refusals, and the answer of a server
    that cannot hold a request (the client is a WSGI one)."""

    def test_it_is_a_get_of_the_session_never_cached(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Content-Type"], "application/json")
        for method in (self.client.post, self.client.put, self.client.delete):
            self.assertEqual(method(self.url).status_code, 405)
        anonymous = Client().get(self.url, HTTP_ACCEPT="application/json")
        self.assertEqual(anonymous.status_code, 401)
        self.assertNotIn("cursor", anonymous.content.decode())

    def test_it_takes_no_account_and_no_write(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.ada)
        self.assertEqual(strict.get(self.url).status_code, 200)     # a read: no token
        self.assertIn(strict.post(self.url).status_code, (403, 405))
        self.tell(self.bob, actor=self.ada)
        cursor = self.client.get(self.url).json()["cursor"]
        data = self.client.get(self.url, {"cursor": cursor, "user": self.bob.pk,
                                          "recipient": self.bob.pk}).json()
        self.assertFalse(data["notifications"])

    def test_a_request_a_browser_sent_from_another_site_is_refused(self):
        for site in ("cross-site", "same-site"):
            with self.subTest(site=site):
                response = self.client.get(self.url, HTTP_SEC_FETCH_SITE=site)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("cursor", response.content.decode())
        self.assertEqual(self.client.get(self.url, HTTP_ORIGIN="https://evil.example.com")
                         .status_code, 403)
        for site in ("same-origin", "none"):
            self.assertEqual(self.client.get(self.url, HTTP_SEC_FETCH_SITE=site).status_code, 200)

    @override_settings(NOTIFY_WAIT_SECONDS=20)
    def test_a_server_that_cannot_hold_answers_at_once_and_says_when_to_ask_again(self):
        cursor = self.client.get(self.url).json()["cursor"]
        started = time.monotonic()
        data = self.client.get(self.url, {"cursor": cursor}).json()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual((data["notifications"], data["folders"]), (False, []))
        self.assertEqual(data["retry"], wait.RETRY_SECONDS)
        self.tell(self.ada, actor=self.bob)
        self.assertTrue(self.client.get(self.url, {"cursor": cursor}).json()["notifications"])


@override_settings(LIVE_REDIS_URL="")
class ThroughTheStackTests(Case):
    """One held request through the host's whole middleware, as ASGI."""

    async def test_it_holds_and_a_folder_wakes_it(self):
        await self.async_client.aforce_login(self.ada)
        first = (await self.async_client.get(self.url, {"folders": str(self.folder.pk)})).json()
        self.assertEqual(first["folders"], [self.folder.pk])
        query = {"cursor": first["cursor"], "folders": str(self.folder.pk)}
        started = time.monotonic()
        quiet = (await self.async_client.get(self.url, query)).json()
        self.assertGreaterEqual(time.monotonic() - started, HOLD)
        self.assertEqual(quiet["folders"], [])
        with override_settings(NOTIFY_WAIT_SECONDS=20):
            held = asyncio.ensure_future(self.async_client.get(self.url, query))
            await asyncio.sleep(0.3)
            self.assertFalse(held.done())
            live.touch(vault_live.folder_key(self.folder.pk))      # the cache alone
            response = await asyncio.wait_for(held, 15)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["folders"], [self.folder.pk])


class _FakePubSub:
    def __init__(self):
        self.said = asyncio.Queue()
        self.channels = []

    async def subscribe(self, channel):
        self.channels.append(channel)

    async def get_message(self, timeout=None):
        return await self.said.get()


class _FakeRedis:
    def __init__(self):
        self.listener = _FakePubSub()
        self.published = []
        self.closed = False

    def pubsub(self, **kwargs):
        return self.listener

    def publish(self, channel, message):
        self.published.append((channel, message))

    async def aclose(self):
        self.closed = True


@override_settings(LIVE_REDIS_URL="redis://live.invalid:6379/0")
class SubscriberTests(SimpleTestCase):
    """The Redis half of ``toto.core.live``, against a faked client: one
    subscriber for the process, a key heard wakes its waiters, and a held
    request asks nothing while the subscriber is up."""

    def setUp(self):
        self.addCleanup(live._listeners.clear)
        self.addCleanup(setattr, live, "_publisher", None)

    async def test_one_subscriber_wakes_the_waiters_of_the_keys_it_hears(self):
        fake = _FakeRedis()
        loop = asyncio.get_running_loop()
        with mock.patch("redis.asyncio.Redis.from_url", return_value=fake) as made:
            state = live._ensure_listener(loop)
            self.assertIs(live._ensure_listener(loop), state)      # one, not one per wait
            await asyncio.sleep(0.05)
        self.assertEqual(made.call_count, 1)
        self.assertEqual(fake.listener.channels, [live.CHANNEL])
        self.assertTrue(live.listening())
        folder, bell, other = live.Waiter(), live.Waiter(), live.Waiter()
        folder.listen(["folder.7"])
        bell.listen(["user.1"])
        other.listen(["user.2"])
        try:
            fake.listener.said.put_nowait({"type": "message", "data": b"another.proc folder.7"})
            # What this process said itself woke its waiters when it said it.
            fake.listener.said.put_nowait(
                {"type": "message", "data": f"{live._origin()} user.1".encode()})
            fake.listener.said.put_nowait({"type": "message", "data": b"junk"})
            await asyncio.sleep(0.05)
            self.assertEqual((folder.event.is_set(), bell.event.is_set(), other.event.is_set()),
                             (True, False, False))
            # Up: the wait is on the event alone, and the stamps are not read.
            with mock.patch.object(live, "stamps", side_effect=AssertionError("polled")):
                self.assertFalse(await live.wait(other, 0.3, seen={"user.2": "old"}))
                self.assertTrue(await live.wait(folder, 0.3, seen={}))
        finally:
            for waiter in (folder, bell, other):
                waiter.close()
            state.task.cancel()
            await asyncio.gather(state.task, return_exceptions=True)
        self.assertTrue(fake.closed)
        self.assertFalse(live.listening())
        self.assertEqual(live.waiting(), 0)

    def test_a_change_is_said_on_the_channel_as_a_key_and_nothing_else(self):
        fake = _FakeRedis()
        with mock.patch("redis.Redis.from_url", return_value=fake) as made:
            live.touch("folder.7")
            live.touch("user.1")
        self.assertEqual(made.call_count, 1)
        self.assertEqual(fake.published, [(live.CHANNEL, f"{live._origin()} folder.7"),
                                          (live.CHANNEL, f"{live._origin()} user.1")])

    def test_a_redis_that_is_away_costs_the_wake_and_never_raises(self):
        with mock.patch("redis.Redis.from_url", side_effect=OSError("down")):
            live.touch("folder.7")
        self.assertTrue(live.stamps(["folder.7"])["folder.7"])

    @override_settings(LIVE_REDIS_URL="")
    def test_without_a_url_nothing_of_redis_is_touched(self):
        with mock.patch("redis.Redis.from_url", side_effect=AssertionError("asked")):
            live.touch("folder.7")
        self.assertEqual(live.redis_url(), "")


class PartsTests(SimpleTestCase):
    def test_the_folders_of_a_poll_are_plain_numbers_each_once_and_capped(self):
        self.assertEqual(wait.folder_ids("3,1,3, 2 ,x,-4,0,1e3,٣,"), [3, 1, 2])
        self.assertEqual(wait.folder_ids(None), [])
        self.assertEqual(len(wait.folder_ids(",".join(str(n) for n in range(1, 500)))),
                         wait.MAX_FOLDERS)
        self.assertEqual(wait.folder_ids("9" * 30), [])

    def test_the_hold_stays_under_a_proxys_minute(self):
        self.assertLess(wait.HOLD_SECONDS, 60)
        self.assertLess(wait.HOLD_LIMIT, 60)
        with override_settings(NOTIFY_WAIT_SECONDS=3600):
            self.assertEqual(wait.hold_seconds(), wait.HOLD_LIMIT)
        with override_settings(NOTIFY_WAIT_SECONDS="x"):
            self.assertEqual(wait.hold_seconds(), wait.HOLD_SECONDS)

    def test_no_socket_is_left_in_the_app(self):
        import importlib.util
        from pathlib import Path

        root = Path(notify.__file__).parent
        for gone in ("consumers", "routing", "ws", "presence", "testing"):
            with self.subTest(module=gone):
                self.assertIsNone(importlib.util.find_spec(f"toto.notify.{gone}"))
        for path in [*root.rglob("*.py"), *root.rglob("*.js"), Path(live.__file__)]:
            if path.name.startswith("tests"):
                continue
            source = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                for word in ("import channels", "from channels", "new WebSocket",
                             "env.WebSocket", "wss://", "group_send"):
                    self.assertNotIn(word, source)
