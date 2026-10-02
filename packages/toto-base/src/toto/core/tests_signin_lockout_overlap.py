"""Sign-in tries at the same moment (2026-10-02, the crown bug hunt).

The sign-in lockout counted a failure only after ``ModelBackend`` had
compared the password and Django had sent ``user_login_failed``; its backend
only READ the counts before the comparison. So a try that arrived while
others were still being compared found nothing counted yet: two hundred
simultaneous ``POST /api/login/`` were two hundred passwords compared, past
the five free tries, the waits and the pause after ten. Now a try is counted
when it begins, before its password is compared, and one past a limit is
refused there; a try that turns out not to be a guess gives its count back.

The comparisons overlap here by nesting: while one try's password is being
compared the next try begins, inside it — what a web server's threads do,
without the scheduler deciding the order. Nothing sleeps; the clock is the
lockout's own (``tests_signin_lockout``).

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.core.tests_signin_lockout_overlap
"""

from unittest import mock

from django.contrib.auth.backends import ModelBackend
from django.test import override_settings

from toto.core import signin_lockout as lockout
from toto.core.tests_signin_lockout import _Lockout, _request


class OverlappingTriesTests(_Lockout):
    def overlap(self, names, password="wrong"):
        """Wrong passwords for ``names``, each try beginning while the one
        before is being compared. (the names compared, the refusals' reasons)"""
        compared, refused = [], []
        pending = list(names)
        real = ModelBackend.authenticate

        def begin_the_rest():
            while pending:
                held = self.attempt(pending.pop(0), password)[1]
                if held is not None:
                    refused.append(held.reason)

        def compare(backend, request, username=None, password=None, **kwargs):
            compared.append(username)
            begin_the_rest()          # the others arrive while this one is compared
            return real(backend, request, username=username, password=password, **kwargs)

        with mock.patch.object(ModelBackend, "authenticate", compare):
            begin_the_rest()
        return compared, refused

    def test_overlapping_tries_get_the_five_free_ones_and_one_after_the_wait(self):
        compared, refused = self.overlap(["ada"] * 40)
        self.assertEqual(len(compared), 6)
        self.assertEqual(set(refused), {lockout.DELAY})
        self.assertEqual(len(refused), 34)
        # The failures counted as they came back: the wait goes on growing.
        self.assertEqual(self.attempt()[1].reason, lockout.DELAY)

    @override_settings(LOGIN_DELAY_AFTER=0)
    def test_without_waits_no_more_than_ten_are_compared_and_the_pair_is_paused(self):
        compared, refused = self.overlap(["ada"] * 40)
        self.assertEqual(len(compared), 10)
        self.assertEqual(set(refused), {lockout.LOCKED})
        user, held = self.attempt()                 # the right password, paused too
        self.assertIsNone(user)
        self.assertEqual(held.reason, lockout.LOCKED)

    @override_settings(LOGIN_DELAY_AFTER=0, LOGIN_LOCK_AFTER=0)
    def test_no_more_than_fifty_from_one_address_whatever_the_names(self):
        compared, refused = self.overlap([f"guess{n}" for n in range(80)])
        self.assertEqual(len(compared), 50)
        self.assertEqual(set(refused), {lockout.ADDRESS_LOCKED})
        self.assertEqual(self.attempt("bob")[1].reason, lockout.ADDRESS_LOCKED)

    @override_settings(LOGIN_DELAY_AFTER=0)
    def test_a_try_is_counted_when_it_begins(self):
        held = [lockout.begin_try(_request(), "ada") for _ in range(12)]
        self.assertEqual(held[:10], [None] * 10)
        self.assertEqual({h.reason for h in held[10:]}, {lockout.LOCKED})

    @override_settings(LOGIN_DELAY_AFTER=0)
    def test_a_try_given_back_is_not_counted(self):
        for _ in range(30):
            request = _request()
            self.assertIsNone(lockout.begin_try(request, "ada"))
            lockout.release_try(request)
        self.assertEqual(self.attempt()[0], self.ada)

    def test_sign_ins_are_never_counted_against_the_address(self):
        with override_settings(LOGIN_ADDRESS_LOCK_AFTER=3):
            for _ in range(10):
                self.assertEqual(self.sign_in(), self.ada)
            self.assertEqual(self.sign_in("bob"), self.bob)
