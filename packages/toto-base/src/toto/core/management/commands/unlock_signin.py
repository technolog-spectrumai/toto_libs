"""Lift a sign-in pause (2026-09-30). CONSOLE ONLY.

    manage.py unlock_signin --user ada              # ada, from every address
    manage.py unlock_signin --address 203.0.113.7   # that address: its own pause and
                                                    # every name tried from it
    manage.py unlock_signin --all                   # everybody, everywhere

The options add up: ``--user ada --address 203.0.113.7`` does both. Reached
from the server's shell (``deploy.py <config> unlock-signin``) and from
nowhere else — no URL, no view, no API: a web door that lifts pauses would
be the first thing a guesser looked for.

The name is taken as typed and counted the way the lockout counts it (case
and spacing do not matter); whether an account has it is neither checked nor
said. An IPv6 address lifts its whole /64, which is what the lockout counts.
Unlocking also forgets the counts, so the next failure starts from one.

Who is paused, and since when, is on the audit chain (``AUTH.LOCKED``, at
``/audit/``); this command's own act is recorded as ``AUTH.UNLOCKED``.

The result is one JSON line on stdout. Refused: nothing to unlock, an
address that is not one, and a cache that did not keep the change.
"""

from __future__ import annotations

import json

from django.apps import apps
from django.core.management.base import BaseCommand

from toto.core import signin_lockout


class Command(BaseCommand):
    help = ("Lift sign-in pauses: --user NAME, --address IP, --all. Console only — "
            "never wire this to a URL.")

    def add_arguments(self, parser):
        parser.add_argument("--user", metavar="USERNAME", default="",
                            help="every pause of this name, from every address")
        parser.add_argument("--address", metavar="IP", default="",
                            help="this address's pause, and every name tried from it")
        parser.add_argument("--all", action="store_true", dest="everything",
                            help="every pause there is")

    def emit(self, payload: dict) -> None:
        self.stdout.write(json.dumps(payload, sort_keys=True))

    def handle(self, *args, **options):
        username = (options["user"] or "").strip()
        address = (options["address"] or "").strip()
        everything = bool(options["everything"])
        if not (username or address or everything):
            self.emit({"ok": False, "error": "say what to unlock: --user NAME, --address IP "
                       "or --all"})
            raise SystemExit(1)
        bucket = signin_lockout.address_bucket(address) if address else ""
        if address and not bucket:
            self.emit({"ok": False, "error": f"{address!r} is not an IP address"})
            raise SystemExit(1)
        try:
            if everything:
                signin_lockout.unlock_all()
            if username:
                signin_lockout.unlock_name(username)
            if bucket:
                signin_lockout.unlock_address(address)
        except Exception as exc:  # noqa: BLE001 - one line the console can read
            self.emit({"ok": False, "error": f"the cache did not take the change "
                       f"({type(exc).__name__}); check that it is running, then run this again"})
            raise SystemExit(1)
        unlocked = {key: value for key, value in (
            ("user", username), ("address", bucket), ("all", everything)) if value}
        payload = {"ok": True, "unlocked": unlocked}
        if not self._record(username, bucket, everything):
            payload["warning"] = "unlocked, but the audit record could not be written"
        self.emit(payload)

    def _record(self, username, address, everything) -> bool:
        if not apps.is_installed("toto.audit"):
            return True
        from toto.audit.identity import on_signin_unlocked

        return on_signin_unlocked(username=username, address=address,
                                  everything=everything) is not None
