"""Receivers core connects in ready() must survive ready() returning.

A local function connected weakly is collected the moment the method that
defined it returns. DEBUG=True hid that (Django's argument check caches the
receiver), so it is asserted on the connection itself: under DEBUG=False —
every deployed profile — SQLite ran without WAL.
"""
import weakref

from django.db.backends.signals import connection_created
from django.test import SimpleTestCase


class HeldStronglyTests(SimpleTestCase):
    def test_the_sqlite_wal_hook_is_held_strongly(self):
        hooks = {entry[0][0]: entry[1] for entry in connection_created.receivers}
        hook = hooks.get("core_sqlite_wal")
        self.assertIsNotNone(hook, "connection_created is not hooked at all")
        self.assertNotIsInstance(hook, weakref.ReferenceType)
