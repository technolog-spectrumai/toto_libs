"""Erase an account and everything that is only theirs (2026-09-29).

    manage.py erase_user USERNAME                     # the report: what would go
    manage.py erase_user USERNAME --confirm USERNAME  # erase it

CONSOLE ONLY. This is reached from the server's shell (over SSH:
``tools/delete_user.py`` → ``deploy.py <config> erase-user``) and from
nowhere else — no URL, no view, no API calls it, and none may: an erase is
irreversible, and the only thing standing between a stolen session and a
wiped colleague would be a button.

What "as if they never existed" can and cannot mean here, said plainly:

* **Erased:** the account, its person, their community and clearance
  memberships, their files (the bytes on this disk too), their
  subscriptions, grants, sessions, tokens, and everything else that cascades
  from the account (Django's own collector decides; the report lists it).
  Since 2026-10-01 (37c.21, ``toto.core.erasure``) also what the cascade
  left: the profile picture's file, the bodies of their files' saved
  versions, their home pin and the addresses only they used, their
  membership application with its references, and the pictures and voice
  recordings they sent in the forum.
* **Kept, detached:** rows other people still need keep their place and lose
  the pointer — a workflow they started, a ledger account (its history is
  the platform's money trail), a forum room they opened, and — since
  2026-09-30 — a vault bucket they owned (it may hold other people's files,
  gateways and clearance keeping; it stays, without an owner, until a
  superuser gives it one or deletes it in Storage → Management). Their forum
  messages keep their text, signed "Former member" without their name or
  picture, and their personal bucket and prepaid ledger account are renamed
  "… — deleted account": none keeps their username.
* **Kept, as written:** the audit chain. Each record is sealed by a hash
  over its content and its predecessor's; removing or rewriting one breaks
  verification of every record after it. Their sign-ins, their changes and
  this erasure stay recorded, by username. The ledger's transactions are
  sealed the same way and stay too.
* **Not reachable from here:** copies outside this database — a remote
  bucket's objects, the forge's own account in Gitea, backups already taken,
  exported files in other people's buckets. The report names what it can see.

The report and the result are one JSON line on stdout (the last line).
Refused: an unknown account, and the last active superuser.

A member's open erasure request (filed on My account — the web files it,
only this carries it out) is marked done by the erase; ``requests_closed``
in the result lists them, and ``PRIVACY.ERASURE_DONE`` records each.
"""

from __future__ import annotations

import json
from collections import defaultdict

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import router, transaction
from django.db.models.deletion import Collector, ProtectedError, RestrictedError


def _label(model) -> str:
    return model._meta.label


def _count(value) -> int:
    try:
        return len(value)
    except TypeError:
        return value.count()


def plan(user) -> dict:
    """What erasing ``user`` does — nothing is written. ``deleted`` and
    ``detached`` are the collector's walk (``socialhub.applications`` reads
    them); ``beyond`` counts what ``toto.core.erasure`` adds to it."""
    from toto.core import erasure

    collector = Collector(using=router.db_for_write(type(user)))
    blockers = []
    try:
        collector.collect([user])
    except (ProtectedError, RestrictedError) as exc:
        blockers = sorted({_label(type(obj)) for obj in exc.args[1]})
    deleted: dict = defaultdict(int)
    for model, objs in collector.data.items():
        deleted[_label(model)] += len(objs)
    for qs in collector.fast_deletes:
        deleted[_label(qs.model)] += _count(qs)
    detached: dict = defaultdict(int)
    for (field, _value), batches in collector.field_updates.items():
        detached[f"{_label(field.model)}.{field.name}"] += sum(_count(b) for b in batches)
    # Only the tables with rows (2026-10-01). The collector hands back a
    # queryset for EVERY relation to the account, empty or not, so the report
    # listed every related table — a hundred lines, nearly all at 0 — and the
    # few the erase would really take were lost among them.
    deleted = {label: n for label, n in deleted.items() if n}
    detached = {label: n for label, n in detached.items() if n}
    notes = []
    vault_files = [f for m, objs in collector.data.items() if _label(m) == "vault.VaultFile"
                   for f in objs]
    remote = [f for f in vault_files if getattr(getattr(f, "bucket", None), "storage_backend", "local")
              not in ("", "local")]
    if remote:
        notes.append(f"{len(remote)} file(s) live in a remote bucket: their rows go, the remote "
                     "objects do not — remove them at the remote store.")
    if detached.get("vault.Bucket.owner"):
        notes.append(f"{detached['vault.Bucket.owner']} bucket(s) they owned stay, without an "
                     "owner: give each a new owner, or delete it, in Storage → Management.")
    if deleted.get("gitea.GiteaAccount"):
        notes.append("The forge (Gitea) keeps its own account for them: remove it in Gitea's "
                     "site administration.")
    beyond, more = erasure.report(user)
    notes.extend(more)
    notes.append("The audit chain keeps their records, by username, and records this erasure: "
                 "a sealed record cannot be removed without breaking every later one.")
    notes.append("Backups taken before now still hold them until they age out.")
    return {"username": user.get_username(), "id": user.pk,
            "superuser": bool(user.is_superuser),
            "deleted": dict(sorted(deleted.items())),
            "detached": dict(sorted(detached.items())),
            "beyond": dict(sorted(beyond.items())),
            "blocked_by": blockers, "notes": notes}


class Command(BaseCommand):
    help = ("Erase an account and everything only theirs. Without --confirm: the report. "
            "Console only — never wire this to a URL.")

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--confirm", metavar="USERNAME", default="",
                            help="erase; must repeat the username exactly")

    def emit(self, payload: dict) -> None:
        self.stdout.write(json.dumps(payload, sort_keys=True))

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        user = User.objects.filter(**{User.USERNAME_FIELD: username}).first()
        if user is None:
            self.emit({"ok": False, "error": f"there is no account called {username!r}"})
            raise SystemExit(1)
        report = plan(user)
        if user.is_superuser and user.is_active and not User.objects.filter(
                is_superuser=True, is_active=True).exclude(pk=user.pk).exists():
            self.emit({"ok": False, "error": "this is the last active superuser: make another "
                       "one first, or nobody can administer the platform", "report": report})
            raise SystemExit(1)
        if report["blocked_by"]:
            self.emit({"ok": False, "error": "rows that may not be deleted still point at them",
                       "report": report})
            raise SystemExit(1)
        if not options["confirm"]:
            self.emit({"ok": True, "erased": False, "report": report})
            return
        if options["confirm"] != username:
            self.emit({"ok": False, "error": "--confirm must repeat the username exactly"})
            raise SystemExit(1)
        from toto.core import erasure

        pk = user.pk
        closed = []
        with transaction.atomic():
            # Their erasure request, filed on My account, is carried out by
            # this run: done before the account goes, while it still points at
            # them, and undone with everything else if the erase fails
            # (2026-10-01).
            if apps.is_installed("toto.socialhub"):
                from toto.socialhub.erasure import close_for_erasure

                closed = close_for_erasure(user)
            # What the cascade leaves (2026-10-01, 37c.21): renamed and
            # deleted here, the bytes once this commits.
            left = erasure.gather(user)
            user.delete()
            erasure.after_delete(left)
            transaction.on_commit(lambda: erasure.after_commit(left))
        self._record(username, pk, report)
        if closed:
            from toto.socialhub.erasure import record_done

            record_done(closed)
        self.emit({"ok": True, "erased": True, "report": report, "requests_closed": closed})

    def _record(self, username, pk, report) -> None:
        if not apps.is_installed("toto.audit"):
            return
        from toto.audit.services import SYSTEM, record

        try:
            with transaction.atomic():
                record("auth.account_erased", app_label="auth", object_type="auth.user",
                       object_id=str(pk), description=username, actor_user=SYSTEM,
                       source="console",
                       metadata={"deleted": report["deleted"], "detached": report["detached"],
                                 "beyond": report.get("beyond", {})})
        except Exception:  # noqa: BLE001 - the erase has happened; say so, do not undo it
            self.stderr.write("the erase is done, but the audit record could not be written")
