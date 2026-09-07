"""Create a handful of start accounts from a JSON document on stdin.

    {"users": [{"username": "anna", "password": "...",
                "staff": true, "superuser": false}, ...]}

**stdin is the whole design.** A password in argv shows up in `ps` and in
shell history; one in the environment lands in `docker inspect`; a file has
to be shredded. A pipe is read once and is gone. The caller is a deployment
tool — a builder's Users tab, or a human with a heredoc — that has just
brought a stack up and needs somebody able to log into it.

**Existing usernames are skipped and reported, never modified — unless
`--reset-existing` says otherwise.** Skipping is the default because taking
over an account merely because a start-up list repeated its name is exactly
what a bootstrap must not do to a caller who did not ask.

`--reset-existing` is for the caller who IS authoritative over the
installation: a builder driving deploy.py on the same machine already holds
the database, so refusing to reset a password there guards nothing — it only
means an operator who locked themselves out has to go around the tool. With
the flag, an existing account is made to MATCH THE ROW exactly: password,
staff, superuser and email. That symmetry is the point — a row is a statement
about what the account should be, and an update that applied half of it would
leave the operator guessing which half.

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



def _default_email(username: str) -> str:
    """An address for an account that was given none.

    EVERY ACCOUNT NEEDS ONE, and that is not tidiness. Gitea refuses to
    auto-register an OIDC identity whose `email` claim is empty — it logs
    "provider doesn't return required fields: email" and drops the user on
    /user/link_account, a form with no local password to link with, because
    password sign-in is deliberately off. This command created superusers with
    `email=""` hardcoded, so the one account every deployment has could never
    sign in to the forge.

    `ADMIN_EMAIL` is honoured first; otherwise the address is derived from the
    username and PLATFORM_DOMAIN. A derived address is not a real mailbox and
    is not meant to be — it is a stable, unique identifier that satisfies the
    services which require the claim. An operator who wants real mail sets
    `email:` on the row.

    `.invalid` is reserved by RFC 2606 precisely so a made-up address cannot
    collide with somebody's real one, and is used when no domain is set.
    """
    import os

    explicit = (os.environ.get("ADMIN_EMAIL", "") or "").strip()
    if explicit and username == (os.environ.get("ADMIN_USERNAME", "admin") or "admin"):
        return explicit
    domain = (os.environ.get("PLATFORM_DOMAIN", "") or "").strip()
    domain = domain.split("//")[-1].split("/")[0].strip()
    if not domain or domain in ("localhost", "127.0.0.1"):
        domain = "localhost.invalid"
    return f"{username}@{domain}"


class Command(BaseCommand):
    help = ("Create up to five start accounts from a JSON document on stdin. "
            "Existing usernames are skipped unless --reset-existing. "
            "Passwords are never echoed.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--stdin", default=None,
            help="Read the JSON from this file instead of stdin. Tests only — "
                 "a real deployment must use the pipe.")
        parser.add_argument(
            "--reset-existing", action="store_true",
            help="Make an existing account match its row — password, staff, "
                 "superuser and email. For a caller that is authoritative "
                 "over this installation; without it such rows are skipped.")

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

        reset_existing = bool(options.get("reset_existing"))
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

            superuser = bool(row.get("superuser"))
            # `board` is placidia's word for the same flag.
            staff = bool(row.get("staff") or row.get("board"))
            level = "superuser" if superuser else "staff" if staff else "viewer"

            existing = User.objects.filter(username=username).first()
            if existing is not None and not reset_existing:
                self.stdout.write(f"{username}: already present — untouched")
                continue

            if existing is not None:
                # Match the row exactly. A row is a statement about what the
                # account should be, so applying half of it — the password but
                # not the flags — would leave the operator guessing which half
                # landed. An email is only replaced when the row names one:
                # a blank field is "say nothing", not "erase it".
                existing.set_password(password)
                existing.is_superuser = superuser
                existing.is_staff = superuser or staff
                existing.is_active = True
                typed_email = (row.get("email") or "").strip()
                if typed_email:
                    existing.email = typed_email
                elif not existing.email:
                    existing.email = _default_email(username)
                existing.save()
                self.stdout.write(f"{username}: reset ({level})")
                continue

            email = (row.get("email") or "").strip() or _default_email(username)
            if superuser:
                User.objects.create_superuser(username=username, email=email,
                                              password=password)
            elif staff:
                User.objects.create_user(username=username, password=password,
                                         email=email,
                                         is_active=True, is_staff=True)
            else:
                User.objects.create_user(username=username, password=password,
                                         email=email, is_active=True)
            self.stdout.write(f"{username}: created ({level})")

        if failed:
            # Good rows are still created; the exit code still reports that
            # something in the payload was wrong.
            sys.exit(1)
