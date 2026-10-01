"""A copy of one account's data, as a zip (2026-10-01, RODO art. 15 and 20).

    manage.py export_user --user USERNAME --out PATH   # write the zip to PATH
    manage.py export_user --user USERNAME --out -      # the zip on stdout

CONSOLE, like ``erase_user``: reached from the server's shell
(``deploy.py <config> export-user``), which uses ``--out -`` to carry the
zip out of the web container without leaving a copy in it. The member's own
door is *Download my data* on My account; both build the same zip
(``toto.core.personal_data``).

The report is one JSON line — on stdout, or on stderr when the zip is on
stdout. An existing PATH is refused, never overwritten. The export is on the
audit chain (``PRIVACY.EXPORT_READY``, source ``console``): counts only.
"""

from __future__ import annotations

import json
import os
import sys

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Write a copy of one account's data as a zip (CSV, JSON and their files)."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, metavar="USERNAME")
        parser.add_argument("--out", required=True, metavar="PATH",
                            help="where to write the zip; - for stdout")

    def emit(self, payload: dict, *, to_stderr: bool) -> None:
        line = json.dumps(payload, sort_keys=True)
        (self.stderr if to_stderr else self.stdout).write(line)

    def handle(self, *args, **options):
        from toto.core.personal_data import write_zip

        User = get_user_model()
        username, out = options["user"], options["out"]
        to_stdout = out == "-"
        user = User.objects.filter(**{User.USERNAME_FIELD: username}).first()
        if user is None:
            self.emit({"ok": False, "error": f"there is no account called {username!r}"},
                      to_stderr=to_stdout)
            raise SystemExit(1)
        if to_stdout:
            summary = write_zip(user, sys.stdout.buffer)
            sys.stdout.buffer.flush()
        else:
            if os.path.exists(out):
                self.emit({"ok": False, "error": f"{out} exists; nothing was overwritten"},
                          to_stderr=False)
                raise SystemExit(1)
            # O_EXCL, and the member's data readable by this account only.
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fileobj:
                summary = write_zip(user, fileobj)
        self._record(user, summary)
        self.emit({"ok": True, "username": username, "out": out, **summary},
                  to_stderr=to_stdout)

    def _record(self, user, summary) -> None:
        if not apps.is_installed("toto.audit"):
            return
        from toto.audit.services import SYSTEM, record

        try:
            with transaction.atomic():
                record("privacy.export_ready", app_label="socialhub", object_type="auth.user",
                       object_id=str(user.pk), description=user.get_username(),
                       actor_user=SYSTEM, source="console",
                       metadata={"user": user.pk, "tables": summary["tables"],
                                 "files": summary["files"]})
        except Exception:  # noqa: BLE001 - the copy is made; say so, do not undo it
            sys.stderr.write("the export is written, but the audit record could not be written\n")
