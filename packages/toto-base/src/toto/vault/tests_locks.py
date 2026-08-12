"""Editing locks: one holder, expiry read rather than swept, no manual breaking."""

import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.vault import locks
from toto.vault.models import FileLock, VaultFile

User = get_user_model()


class LockTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp(prefix="vault-locks-")
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.temp_media, ignore_errors=True)

    def setUp(self):
        self._media = override_settings(MEDIA_ROOT=self.temp_media)
        self._media.enable()
        self.addCleanup(self._media.disable)
        self.anna = User.objects.create_user(username="anna")
        self.piotr = User.objects.create_user(username="piotr")
        self.file = VaultFile(owner=self.anna, title="Doc", key="doc",
                              file_type="document")
        self.file.file.save("doc.xml", ContentFile(b"<document/>"), save=False)
        self.file.save()

    def _expire(self):
        FileLock.objects.filter(file=self.file).update(
            expires_at=timezone.now() - timedelta(seconds=1))


class AcquireTests(LockTestCase):
    def test_the_first_caller_gets_it(self):
        lock = locks.acquire(self.file, self.anna)
        self.assertEqual(lock.holder, self.anna)
        self.assertTrue(lock.is_live)

    def test_a_second_person_is_refused_and_told_who_has_it(self):
        locks.acquire(self.file, self.anna)
        with self.assertRaises(locks.Locked) as caught:
            locks.acquire(self.file, self.piotr)
        self.assertEqual(caught.exception.lock.holder, self.anna)

    def test_two_people_cannot_both_hold_one_file(self):
        # The OneToOne is the guarantee, not the service around it.
        locks.acquire(self.file, self.anna)
        try:
            locks.acquire(self.file, self.piotr)
        except locks.Locked:
            pass
        self.assertEqual(FileLock.objects.filter(file=self.file).count(), 1)

    def test_reacquiring_your_own_lock_refreshes_rather_than_refuses(self):
        # A user must never be locked out of their own document — their second
        # tab is layer two's problem, not layer one's.
        first = locks.acquire(self.file, self.anna)
        FileLock.objects.filter(pk=first.pk).update(
            expires_at=timezone.now() + timedelta(seconds=5))
        again = locks.acquire(self.file, self.anna)
        self.assertEqual(again.pk, first.pk)
        self.assertGreater(again.expires_at, timezone.now() + timedelta(seconds=30))

    def test_an_expired_lock_is_takeable_with_nobody_breaking_anything(self):
        locks.acquire(self.file, self.anna)
        self._expire()

        lock = locks.acquire(self.file, self.piotr)

        self.assertEqual(lock.holder, self.piotr)
        self.assertEqual(FileLock.objects.filter(file=self.file).count(), 1)

    def test_an_anonymous_caller_never_holds_a_lock(self):
        with self.assertRaises(locks.Locked):
            locks.acquire(self.file, AnonymousUser())
        self.assertFalse(FileLock.objects.exists())

    def test_locks_are_per_file(self):
        other = VaultFile(owner=self.anna, title="Other", key="other",
                          file_type="document")
        other.file.save("other.xml", ContentFile(b"<document/>"), save=False)
        other.save()

        locks.acquire(self.file, self.anna)
        locks.acquire(other, self.piotr)      # different file, no contention

        self.assertEqual(locks.holder_of(self.file).holder, self.anna)
        self.assertEqual(locks.holder_of(other).holder, self.piotr)


class ExpiryTests(LockTestCase):
    def test_an_expired_lock_is_simply_not_a_lock(self):
        # Read, never swept: no cron to fall behind, and a host with a dead
        # worker does not slowly lock its whole vault.
        locks.acquire(self.file, self.anna)
        self._expire()

        self.assertIsNone(locks.holder_of(self.file))
        self.assertTrue(locks.may_write(self.file, self.piotr))

    def test_a_heartbeat_keeps_it(self):
        locks.acquire(self.file, self.anna)
        FileLock.objects.filter(file=self.file).update(
            expires_at=timezone.now() + timedelta(seconds=1))

        self.assertTrue(locks.heartbeat(self.file, self.anna))
        self.assertGreater(locks.holder_of(self.file).expires_at,
                           timezone.now() + timedelta(seconds=30))

    def test_a_heartbeat_from_someone_who_lost_it_says_so(self):
        # How a client that went to sleep finds out, on its next beat.
        locks.acquire(self.file, self.anna)
        self.assertFalse(locks.heartbeat(self.file, self.piotr))

    def test_a_heartbeat_cannot_revive_an_expired_lock(self):
        locks.acquire(self.file, self.anna)
        self._expire()
        self.assertFalse(locks.heartbeat(self.file, self.anna))


class ReleaseTests(LockTestCase):
    def test_the_holder_can_give_it_up(self):
        locks.acquire(self.file, self.anna)
        self.assertTrue(locks.release(self.file, self.anna))
        self.assertIsNone(locks.holder_of(self.file))

    def test_a_non_holder_releasing_is_a_no_op(self):
        locks.acquire(self.file, self.anna)
        self.assertFalse(locks.release(self.file, self.piotr))
        self.assertEqual(locks.holder_of(self.file).holder, self.anna)

    def test_releasing_an_unlocked_file_is_harmless(self):
        self.assertFalse(locks.release(self.file, self.anna))


class MayWriteTests(LockTestCase):
    def test_an_unlocked_file_is_writable_by_anyone(self):
        # Absence of a lock is not a refusal — otherwise shipping this would
        # have broken every editor already open in somebody's browser.
        self.assertTrue(locks.may_write(self.file, self.piotr))

    def test_the_holder_may_write(self):
        locks.acquire(self.file, self.anna)
        self.assertTrue(locks.may_write(self.file, self.anna))

    def test_everyone_else_may_not(self):
        locks.acquire(self.file, self.anna)
        self.assertFalse(locks.may_write(self.file, self.piotr))

    def test_once_it_expires_everyone_may_again(self):
        locks.acquire(self.file, self.anna)
        self._expire()
        self.assertTrue(locks.may_write(self.file, self.piotr))

    def test_the_heartbeat_interval_stays_under_the_ttl(self):
        # If these ever crossed, every editor would drop its own lock mid-edit.
        self.assertLess(timedelta(seconds=locks.HEARTBEAT_SECONDS) * 2,
                        locks.LOCK_TTL)
