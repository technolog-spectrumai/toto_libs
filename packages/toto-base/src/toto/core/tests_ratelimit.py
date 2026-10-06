from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase

from toto.core import ratelimit


class RateLimitTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_the_limit_is_inclusive_and_the_next_one_is_refused(self):
        for n in range(3):
            self.assertTrue(ratelimit.hit("k", limit=3, window=60, now=1000).allowed)
        refused = ratelimit.hit("k", limit=3, window=60, now=1000)
        self.assertFalse(refused.allowed)
        self.assertLessEqual(refused.retry_after, 60)
        self.assertGreater(refused.retry_after, 0)

    def test_a_new_window_is_a_new_counter(self):
        for _ in range(4):
            ratelimit.hit("k", limit=3, window=60, now=1000)
        self.assertTrue(ratelimit.hit("k", limit=3, window=60, now=1061).allowed)

    def test_keys_are_independent(self):
        for _ in range(4):
            ratelimit.hit("a", limit=3, window=60, now=1000)
        self.assertTrue(ratelimit.hit("b", limit=3, window=60, now=1000).allowed)

    def test_check_raises_with_a_retry_after(self):
        ratelimit.check("c", limit=1, window=60)
        with self.assertRaises(ratelimit.RateLimited) as caught:
            ratelimit.check("c", limit=1, window=60)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertGreaterEqual(caught.exception.retry_after, 1)

    def test_a_broken_cache_fails_open(self):
        with mock.patch.object(ratelimit.cache, "incr", side_effect=ValueError("down")):
            self.assertTrue(ratelimit.hit("d", limit=0, window=60).allowed)

    def test_a_hit_says_whether_it_was_counted(self):
        """Failing open is the rule, and a caller that must not go
        uncounted (an outside service asked on a member's behalf) has to be
        able to tell an allowed attempt from one nobody counted."""
        self.assertTrue(ratelimit.hit("counted", limit=1, window=60).counted)
        refused = ratelimit.hit("counted", limit=1, window=60)
        self.assertEqual((refused.allowed, refused.counted), (False, True))
        with mock.patch.object(ratelimit.cache, "incr", side_effect=ConnectionError("down")), \
                self.assertLogs("toto.core.ratelimit", "WARNING"):
            blind = ratelimit.hit("counted", limit=1, window=60)
        self.assertEqual((blind.allowed, blind.counted), (True, False))
        with mock.patch.object(ratelimit.cache, "incr", return_value=None), \
                self.assertLogs("toto.core.ratelimit", "WARNING"):
            blind = ratelimit.hit("counted", limit=1, window=60)
        self.assertEqual((blind.allowed, blind.counted), (True, False))
        with mock.patch.object(ratelimit.cache, "add", side_effect=ConnectionError("down")), \
                self.assertLogs("toto.core.ratelimit", "WARNING"):
            self.assertFalse(ratelimit.hit("counted", limit=1, window=60).counted)

    def test_a_hit_built_by_hand_counts_unless_it_says_otherwise(self):
        self.assertTrue(ratelimit.Hit(True, 3, 0).counted)
        self.assertFalse(ratelimit.Hit(True, 3, 0, counted=False).counted)

    def test_check_still_lets_an_uncounted_attempt_through(self):
        with mock.patch.object(ratelimit.cache, "incr", return_value=None), \
                self.assertLogs("toto.core.ratelimit", "WARNING"):
            self.assertFalse(ratelimit.check("open", limit=0, window=60).counted)

    def test_reset(self):
        ratelimit.hit("e", limit=1, window=60, now=1000)
        ratelimit.reset("e", window=60, now=1000)
        self.assertTrue(ratelimit.hit("e", limit=1, window=60, now=1000).allowed)


class CountAndHoldTests(SimpleTestCase):
    """The two shapes the sign-in lockout needs (2026-09-30): a count that
    lives while failures keep coming, and a deadline set once."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_a_count_goes_up_by_one_and_peek_does_not_count(self):
        self.assertEqual([ratelimit.count("n", window=60) for _ in range(3)], [1, 2, 3])
        self.assertEqual((ratelimit.peek("n"), ratelimit.peek("n")), (3, 3))
        self.assertEqual(ratelimit.peek("never"), 0)

    def test_every_count_pushes_the_forgetting_back(self):
        with mock.patch.object(ratelimit.cache, "touch", wraps=ratelimit.cache.touch) as touch:
            ratelimit.count("n", window=90)
            ratelimit.count("n", window=90)
        self.assertEqual([c.args for c in touch.call_args_list], [("rl:n:n", 90)] * 2)

    def test_a_count_on_a_broken_cache_is_none_and_logged(self):
        with mock.patch.object(ratelimit.cache, "incr", side_effect=ValueError("down")), \
                self.assertLogs("toto.core.ratelimit", "WARNING"):
            self.assertIsNone(ratelimit.count("n", window=60))
        with mock.patch.object(ratelimit.cache, "incr", return_value=None), \
                self.assertLogs("toto.core.ratelimit", "WARNING"):
            self.assertIsNone(ratelimit.count("n", window=60))

    def test_a_hold_is_taken_once_and_read_until_its_deadline(self):
        self.assertTrue(ratelimit.hold("h", seconds=30, now=1000))
        self.assertFalse(ratelimit.hold("h", seconds=30, now=1010))
        self.assertEqual(ratelimit.held_until("h", now=1029), 1030)
        self.assertEqual(ratelimit.held_until("h", now=1030), 0.0)

    def test_a_hold_past_its_deadline_is_free_to_take_again(self):
        ratelimit.hold("h", seconds=30, now=1000)
        self.assertTrue(ratelimit.hold("h", seconds=30, now=1031))
        self.assertEqual(ratelimit.held_until("h", now=1040), 1061)

    def test_replace_moves_the_deadline_either_way(self):
        ratelimit.hold("h", seconds=30, now=1000)
        self.assertTrue(ratelimit.hold("h", seconds=4, replace=True, now=1000))
        self.assertEqual(ratelimit.held_until("h", now=1000), 1004)

    def test_forget_drops_counts_and_holds(self):
        ratelimit.count("a", window=60)
        ratelimit.hold("a", seconds=60)
        ratelimit.hold("b", seconds=60)
        ratelimit.forget("a", "b")
        self.assertEqual((ratelimit.peek("a"), ratelimit.held_until("a"),
                          ratelimit.held_until("b")), (0, 0.0, 0.0))

    def test_a_broken_cache_holds_nobody(self):
        with mock.patch.object(ratelimit.cache, "add", side_effect=ConnectionError("down")), \
                self.assertLogs("toto.core.ratelimit", "WARNING"):
            self.assertFalse(ratelimit.hold("h", seconds=60))
        with mock.patch.object(ratelimit.cache, "get", side_effect=ConnectionError("down")), \
                self.assertLogs("toto.core.ratelimit", "WARNING"):
            self.assertEqual(ratelimit.held_until("h"), 0.0)
        with mock.patch.object(ratelimit.cache, "get", side_effect=ConnectionError("down")):
            self.assertEqual(ratelimit.peek("h"), 0)
        with mock.patch.object(ratelimit.cache, "delete_many", side_effect=ConnectionError("down")):
            ratelimit.forget("h")
