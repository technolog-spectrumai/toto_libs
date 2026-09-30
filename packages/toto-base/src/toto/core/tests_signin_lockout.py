"""The sign-in lockout (2026-09-30): growing waits, then a pause, per name and
address — and never on the account itself.

These drive ``authenticate(request, ...)`` directly, which is where every
password door meets the lockout; the doors themselves (the form, the API, the
admin) are ``tests_signin_doors``. The clock is the lockout's own, moved by
hand: nothing here sleeps."""

import io
import json
from contextlib import contextmanager
from unittest import mock

from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings

from toto.audit.models import AuditRecord
from toto.core import ratelimit
from toto.core import signin_lockout as lockout

User = get_user_model()

BACKENDS = [lockout.BACKEND_PATH, "django.contrib.auth.backends.ModelBackend"]
RIGHT = "Correct-horse-9"
HERE = "203.0.113.7"
ELSEWHERE = "198.51.100.20"

RULES = dict(AUTHENTICATION_BACKENDS=BACKENDS, LOGIN_DELAY_AFTER=5, LOGIN_DELAY_MAX_SECONDS=60,
             LOGIN_LOCK_AFTER=10, LOGIN_LOCK_MINUTES=15, LOGIN_ADDRESS_LOCK_AFTER=50,
             LOGIN_FAILURE_WINDOW_MINUTES=15,
             # Hundreds of wrong passwords per run: the real hasher would make
             # this suite a minute long, and it tests nothing about hashing.
             PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])


def _request(address=HERE):
    # No proxy in front: the peer is the visitor.
    return RequestFactory().post("/sso/login/", REMOTE_ADDR=address)


@override_settings(**RULES)
class _Lockout(TestCase):
    """A clean cache before and after each test: the counters live there, not
    in the database the test case rolls back."""

    @classmethod
    def setUpTestData(cls):
        cls.ada = User.objects.create_user("ada", password=RIGHT)
        cls.bob = User.objects.create_user("bob", password=RIGHT)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.now = 1_800_000_000.0
        patcher = mock.patch.object(lockout, "_clock", side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)

    def wait(self, seconds):
        self.now += seconds

    def attempt(self, username="ada", password=RIGHT, address=HERE):
        """(user or None, the lockout's refusal or None)."""
        request = _request(address)
        user = authenticate(request, username=username, password=password)
        return user, lockout.refusal_for(request)

    def sign_in(self, username="ada", address=HERE):
        """The whole door: the password, then the session (user_logged_in)."""
        request = _request(address)
        SessionMiddleware(lambda r: None).process_request(request)
        user = authenticate(request, username=username, password=RIGHT)
        if user is not None:
            login(request, user)
        return user

    def guess_wrong(self, times, username="ada", address=HERE):
        """``times`` counted failures: each after any wait has run out."""
        for _ in range(times):
            self.wait(61)
            user, held = self.attempt(username, "wrong", address)
            self.assertIsNone(user)
            self.assertIsNone(held, "a failure meant to be counted was refused instead")

    def records(self, action):
        return AuditRecord.objects.filter(action=action).order_by("sequence")


class WaitTests(_Lockout):
    def test_five_failures_are_free_and_then_even_the_right_password_waits_a_second(self):
        self.guess_wrong(5)
        user, held = self.attempt()
        self.assertIsNone(user)
        self.assertEqual((held.reason, held.retry_after), (lockout.DELAY, 1))
        self.wait(1)
        user, held = self.attempt()
        self.assertEqual(user, self.ada)
        self.assertIsNone(held)

    def test_four_failures_ask_for_no_wait(self):
        self.guess_wrong(4)
        self.assertEqual(self.attempt()[0], self.ada)

    def test_the_wait_doubles_with_every_failure_after_the_fifth(self):
        waits = []
        for _ in range(5):
            self.guess_wrong(1 if waits else 5)
            waits.append(self.attempt("ada", "wrong")[1].retry_after)
        self.assertEqual(waits, [1, 2, 4, 8, 16])

    @override_settings(LOGIN_LOCK_AFTER=0)
    def test_the_wait_never_passes_the_cap(self):
        self.guess_wrong(13)
        self.assertEqual(self.attempt()[1].retry_after, 60)

    def test_a_refused_try_is_not_one_more_guess(self):
        self.guess_wrong(6)                           # the wait is 2 s now
        for _ in range(20):
            self.assertEqual(self.attempt("ada", "wrong")[1].reason, lockout.DELAY)
        self.guess_wrong(1)
        # Seven counted, not twenty-seven: the wait is 4 s, and no pause.
        self.assertEqual(self.attempt()[1].retry_after, 4)

    def test_a_sign_in_clears_the_pairs_count(self):
        self.guess_wrong(4)
        self.assertEqual(self.sign_in(), self.ada)
        self.guess_wrong(4)
        self.assertEqual(self.attempt()[0], self.ada)

    def test_without_the_sign_in_the_count_goes_on(self):
        self.guess_wrong(4)
        self.assertEqual(self.attempt()[0], self.ada)   # the password, but no session
        self.guess_wrong(4)
        self.assertEqual(self.attempt()[1].reason, lockout.DELAY)

    def test_the_name_is_counted_as_typed_whatever_its_case_or_spacing(self):
        for spelling in ("Ada", "ADA", " ada", "ada ", "ａｄａ"):
            self.guess_wrong(1, username=spelling)
        self.assertEqual(self.attempt()[1].reason, lockout.DELAY)


class PauseTests(_Lockout):
    def test_ten_failures_pause_the_pair_for_fifteen_minutes_right_password_too(self):
        self.guess_wrong(10)
        user, held = self.attempt()
        self.assertIsNone(user)
        self.assertEqual((held.reason, held.retry_after), (lockout.LOCKED, 900))
        self.wait(899)
        self.assertEqual(self.attempt()[1].reason, lockout.LOCKED)
        self.wait(1)
        self.assertEqual(self.attempt()[0], self.ada)

    def test_after_a_pause_the_pair_starts_again_from_nothing(self):
        self.guess_wrong(10)
        self.wait(900)
        self.guess_wrong(4)
        self.assertEqual(self.attempt()[0], self.ada)

    def test_the_account_is_never_paused_only_the_address_that_guessed(self):
        self.guess_wrong(10)
        self.assertEqual(self.attempt(address=ELSEWHERE)[0], self.ada)

    def test_another_name_from_the_same_address_is_not_paused_with_it(self):
        self.guess_wrong(10)
        self.assertEqual(self.attempt("bob")[0], self.bob)

    def test_fifty_failures_from_one_address_pause_every_name_from_it(self):
        for n in range(10):
            self.guess_wrong(5, username=f"guess{n}")
        user, held = self.attempt("bob")
        self.assertIsNone(user)
        self.assertEqual((held.reason, held.retry_after), (lockout.ADDRESS_LOCKED, 900))
        self.assertEqual(self.attempt("bob", address=ELSEWHERE)[0], self.bob)
        self.wait(900)
        self.assertEqual(self.attempt("bob")[0], self.bob)

    def test_an_unknown_name_is_paused_exactly_like_a_known_one(self):
        for failures in range(1, 11):
            self.wait(61)
            for name in ("ghost", "ada"):
                self.assertIsNone(self.attempt(name, "wrong")[0])
            if failures in (5, 10):
                # A wait, then a pause: the same answer at every step.
                ghost, known = self.attempt("ghost")[1], self.attempt("ada")[1]
                self.assertEqual((ghost.reason, ghost.retry_after, ghost.message),
                                 (known.reason, known.retry_after, known.message))
        self.assertEqual(known.reason, lockout.LOCKED)

    def test_ipv6_counts_per_64(self):
        self.guess_wrong(10, address="2001:db8:1:2::a")
        self.assertEqual(self.attempt(address="2001:db8:1:2:ffff::b")[1].reason, lockout.LOCKED)
        self.assertEqual(self.attempt(address="2001:db8:1:3::a")[0], self.ada)

    def test_a_pause_costs_no_password_check(self):
        self.guess_wrong(10)
        with mock.patch("django.contrib.auth.backends.ModelBackend.authenticate") as checked:
            self.attempt()
        checked.assert_not_called()


class ScopeTests(_Lockout):
    def test_without_a_request_nothing_is_counted_or_refused(self):
        for _ in range(30):
            self.assertIsNone(authenticate(None, username="ada", password="wrong"))
        self.assertEqual(authenticate(None, username="ada", password=RIGHT), self.ada)
        self.assertEqual(self.attempt()[0], self.ada)

    def test_a_request_with_no_address_is_neither_counted_nor_refused(self):
        request = RequestFactory().post("/")
        del request.META["REMOTE_ADDR"]
        for _ in range(12):
            authenticate(request, username="ada", password="wrong")
        self.assertEqual(authenticate(request, username="ada", password=RIGHT), self.ada)

    @override_settings(AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.ModelBackend"])
    def test_a_host_without_the_backend_counts_nothing(self):
        with mock.patch.object(lockout, "note_failure") as noted:
            self.guess_wrong(12)
        noted.assert_not_called()

    @override_settings(LOGIN_DELAY_AFTER=0, LOGIN_LOCK_AFTER=0, LOGIN_ADDRESS_LOCK_AFTER=0)
    def test_zero_turns_each_rule_off(self):
        self.guess_wrong(60)
        self.assertEqual(self.attempt()[0], self.ada)

    def test_a_cache_that_cannot_answer_holds_nobody(self):
        self.guess_wrong(10)
        with mock.patch.object(lockout.cache, "get_many", side_effect=ConnectionError("down")), \
                self.assertLogs("toto.core.signin_lockout", "WARNING"):
            self.assertEqual(self.attempt()[0], self.ada)

    def test_a_cache_that_cannot_count_counts_nothing(self):
        with mock.patch.object(ratelimit.cache, "incr", side_effect=ConnectionError("down")), \
                self.assertLogs("toto.core.ratelimit", "WARNING"):
            self.guess_wrong(12)
        self.assertEqual(self.attempt()[0], self.ada)

    def test_the_refusal_is_a_sentence_with_the_time_left(self):
        self.assertEqual(lockout.Refusal(lockout.DELAY, 1).message,
                         "Too many failed sign-in attempts. Wait 1 second and try again.")
        self.assertEqual(lockout.Refusal(lockout.DELAY, 16).message,
                         "Too many failed sign-in attempts. Wait 16 seconds and try again.")
        self.assertEqual(lockout.Refusal(lockout.LOCKED, 900).message,
                         "Too many failed sign-in attempts. Signing in is paused for 15 minutes.")
        self.assertEqual(lockout.Refusal(lockout.ADDRESS_LOCKED, 30).message,
                         "Too many failed sign-in attempts. Signing in is paused for 1 minute.")


class ChainTests(_Lockout):
    def dump(self, records):
        return json.dumps([[r.metadata, r.changes, r.object_description, r.request_source]
                           for r in records])

    def test_a_paused_pair_is_one_locked_record_with_the_name_and_the_address(self):
        self.guess_wrong(10)
        for _ in range(3):
            self.attempt()
        locked = self.records("AUTH.LOCKED").get()
        self.assertFalse(locked.success)
        self.assertIsNone(locked.actor_user)
        self.assertEqual(locked.metadata, {"scope": "account_address", "username": "ada",
                                           "address": HERE, "failures": 10, "minutes": 15})
        self.assertEqual(locked.object_description, "ada")
        self.assertNotIn(RIGHT, self.dump(AuditRecord.objects.all()))
        self.assertNotIn('"wrong"', self.dump(AuditRecord.objects.all()))

    def test_a_paused_address_is_its_own_record(self):
        for n in range(10):
            self.guess_wrong(5, username=f"guess{n}")
        locked = self.records("AUTH.LOCKED").get()
        self.assertEqual(locked.metadata, {"scope": "address", "address": HERE,
                                           "failures": 50, "minutes": 15})
        self.assertEqual(locked.object_description, HERE)

    def test_refused_tries_reach_the_chain_once_a_minute_and_say_why(self):
        self.guess_wrong(10)
        before = self.records("AUTH.LOGIN_FAILED").count()
        for _ in range(5):
            self.attempt()
        refused = list(self.records("AUTH.LOGIN_FAILED"))[before:]
        self.assertEqual([r.metadata for r in refused], [{"username": "ada", "refused": "locked"}])
        self.wait(61)
        self.attempt()
        self.assertEqual(self.records("AUTH.LOGIN_FAILED").count(), before + 2)

    def test_every_counted_failure_is_still_recorded(self):
        self.guess_wrong(7)
        self.assertEqual(self.records("AUTH.LOGIN_FAILED").count(), 7)
        self.assertFalse(any("refused" in r.metadata for r in self.records("AUTH.LOGIN_FAILED")))


class UnlockCommandTests(_Lockout):
    @contextmanager
    def command(self, *argv, fails=False):
        out = io.StringIO()
        if fails:
            with self.assertRaises(SystemExit) as caught:
                call_command("unlock_signin", *argv, stdout=out)
            self.assertEqual(caught.exception.code, 1)
        else:
            call_command("unlock_signin", *argv, stdout=out)
        yield json.loads(out.getvalue().strip().splitlines()[-1])

    def test_user_lifts_the_name_from_every_address_and_no_other_name(self):
        self.guess_wrong(10, address=HERE)
        self.guess_wrong(10, address=ELSEWHERE)
        self.guess_wrong(10, username="bob")
        with self.command("--user", "ADA") as answer:
            self.assertEqual(answer, {"ok": True, "unlocked": {"user": "ADA"}})
        self.assertEqual(self.attempt(address=HERE)[0], self.ada)
        self.assertEqual(self.attempt(address=ELSEWHERE)[0], self.ada)
        self.assertEqual(self.attempt("bob")[1].reason, lockout.LOCKED)

    def test_user_also_forgets_the_count(self):
        self.guess_wrong(9)
        with self.command("--user", "ada"):
            pass
        self.guess_wrong(4)
        self.assertEqual(self.attempt()[0], self.ada)

    def test_address_lifts_its_pause_and_every_name_tried_from_it(self):
        for n in range(10):
            self.guess_wrong(5, username=f"guess{n}")
        self.guess_wrong(10, address=ELSEWHERE)
        self.assertEqual(self.attempt("bob")[1].reason, lockout.ADDRESS_LOCKED)
        with self.command("--address", HERE) as answer:
            self.assertEqual(answer["unlocked"], {"address": HERE})
        self.assertEqual(self.attempt("bob")[0], self.bob)
        self.assertEqual(self.attempt(address=ELSEWHERE)[1].reason, lockout.LOCKED)

    def test_an_ipv6_address_lifts_its_64(self):
        self.guess_wrong(10, address="2001:db8:1:2::a")
        with self.command("--address", "2001:db8:1:2::77") as answer:
            self.assertEqual(answer["unlocked"], {"address": "2001:db8:1:2::/64"})
        self.assertEqual(self.attempt(address="2001:db8:1:2::a")[0], self.ada)

    def test_all_lifts_everything(self):
        self.guess_wrong(10)
        for n in range(10):
            self.guess_wrong(5, username=f"guess{n}", address=ELSEWHERE)
        with self.command("--all") as answer:
            self.assertEqual(answer["unlocked"], {"all": True})
        self.assertEqual(self.attempt()[0], self.ada)
        self.assertEqual(self.attempt("bob", address=ELSEWHERE)[0], self.bob)

    def test_the_unlock_is_on_the_chain_as_a_console_act(self):
        with self.command("--user", "ada", "--address", HERE):
            pass
        record = self.records("AUTH.UNLOCKED").get()
        self.assertEqual(record.metadata, {"username": "ada", "address": HERE})
        self.assertEqual((record.source, record.actor_user), ("console", None))

    def test_nothing_named_is_refused(self):
        with self.command(fails=True) as answer:
            self.assertFalse(answer["ok"])
            self.assertIn("--user NAME, --address IP or --all", answer["error"])
        self.assertFalse(self.records("AUTH.UNLOCKED").exists())

    def test_an_address_that_is_not_one_is_refused(self):
        with self.command("--address", "localhost", fails=True) as answer:
            self.assertEqual(answer["error"], "'localhost' is not an IP address")

    def test_a_cache_that_does_not_keep_the_change_is_said_and_not_recorded(self):
        with mock.patch.object(lockout.cache, "get", return_value=None), \
                self.command("--all", fails=True) as answer:
            self.assertIn("the cache did not take the change", answer["error"])
        self.assertFalse(self.records("AUTH.UNLOCKED").exists())

    def test_a_chain_that_cannot_be_written_is_a_warning_not_a_failure(self):
        with mock.patch("toto.audit.identity._record", return_value=None), \
                self.command("--user", "ada") as answer:
            self.assertTrue(answer["ok"])
            self.assertIn("audit record could not be written", answer["warning"])


class SignalTests(_Lockout):
    def test_a_sign_in_by_any_door_clears_the_pair(self):
        self.guess_wrong(4)
        request = _request()
        SessionMiddleware(lambda r: None).process_request(request)
        login(request, self.ada, backend="django.contrib.auth.backends.ModelBackend")
        self.guess_wrong(4)
        self.assertEqual(self.attempt()[0], self.ada)

    def test_a_failure_with_no_password_in_it_is_not_a_guess(self):
        from django.contrib.auth.signals import user_login_failed

        with mock.patch.object(lockout, "note_failure") as noted:
            user_login_failed.send(sender=__name__, credentials={"token": "x"},
                                   request=_request())
        noted.assert_not_called()
