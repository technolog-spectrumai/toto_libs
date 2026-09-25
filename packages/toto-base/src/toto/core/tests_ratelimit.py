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

    def test_reset(self):
        ratelimit.hit("e", limit=1, window=60, now=1000)
        ratelimit.reset("e", window=60, now=1000)
        self.assertTrue(ratelimit.hit("e", limit=1, window=60, now=1000).allowed)
