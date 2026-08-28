"""Create a handful of start accounts from a JSON document on stdin.

    {"users": [{"username": "anna", "password": "...",
                "staff": true, "superuser": false}, ...]}

**stdin is the whole design.** A password in argv shows up in `ps` and in
shell history; one in the environment lands in `docker inspect`; a file has
to be shredded. A pipe is read once and is gone. The caller is a deployment
tool — a builder's Users tab, or a human with a heredoc — that has just
brought a stack up and needs somebody able to log into it.

**Existing usernames are skipped and reported, never modified.** That is the
whole difference from `create_user` next door, which does `update_or_create`
and is the right tool for "make this account be this way". Taking over an
account because a start-up list happened to repeat its name is exactly what a
bootstrap must not do, so this one refuses to.

Three power levels, and superuser implies staff because Django's own
`create_superuser` does:

* **superuser** — everything, the Django admin included.
* **staff** — `is_staff`; on most hosts that is what gates every write.
* **viewer** — a plain active account.

`board` is accepted as a synonym for `staff`: placidia's truth book calls the
writing role that (its §Dk2), and a host's vocabulary should not force a
second payload shape.
"""

from __future__ import annotations

import json
import sys

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

User = get_user_model()

#: Small on purpose: this is a bootstrap, not a user-import pipeline. A list
#: longer than this is a sign somebody is using the wrong tool, so it is
#: refused WHOLE rather than half-applied.
MAX_USERS = 5


class Command(BaseCommand):
    help = ("Create up to five start accounts from a JSON document on stdin. "
            "Existing usernames are skipped. Passwords are never echoed.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--stdin", default=None,
            help="Read the JSON from this file instead of stdin. Tests only — "
                 "a real deployment must use the pipe.")

    def handle(self, *args, **options):
        source = options.get("stdin")
        if source:
            with open(source, encoding="utf-8") as handle:
                raw = handle.read()
        else:
            raw = sys.stdin.read()

        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            self.stderr.write(f"stdin is not valid JSON: {exc}")
            sys.exit(1)

        users = (payload or {}).get("users") or []
        if not users:
            self.stdout.write("nothing to do — no users in the payload")
            return

        if len(users) > MAX_USERS:
            self.stderr.write(f"{len(users)} users given; this creates at "
                              f"most {MAX_USERS}")
            sys.exit(1)

        failed = False
        for row in users:
            username = str((row or {}).get("username") or "").strip()
            password = (row or {}).get("password") or ""
            if not username or not password:
                self.stderr.write("a row without username or password was "
                                  "skipped")
                failed = True
                continue

            try:
                User.username_validator(username)
            except ValidationError:
                self.stderr.write(f"{username}: not a valid username")
                failed = True
                continue

            if User.objects.filter(username=username).exists():
                self.stdout.write(f"{username}: already present — untouched")
                continue

            superuser = bool(row.get("superuser"))
            # `board` is placidia's word for the same flag.
            staff = bool(row.get("staff") or row.get("board"))
            if superuser:
                User.objects.create_superuser(username=username, email="",
                                              password=password)
                self.stdout.write(f"{username}: created (superuser)")
            elif staff:
                User.objects.create_user(username=username, password=password,
                                         is_active=True, is_staff=True)
                self.stdout.write(f"{username}: created (staff)")
            else:
                User.objects.create_user(username=username, password=password,
                                         is_active=True)
                self.stdout.write(f"{username}: created (viewer)")

        if failed:
            # Good rows are still created; the exit code still reports that
            # something in the payload was wrong.
            sys.exit(1)
