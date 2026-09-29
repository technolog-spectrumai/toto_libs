"""Erase an account and everything that is only theirs (2026-09-29).

    manage.py erase_user USERNAME                     # the report: what would go
    manage.py erase_user USERNAME --confirm USERNAME  # erase it

CONSOLE ONLY. This is reached from the server's shell (over SSH:
``tools/delete_user.py`` → ``deploy.py <config> erase-user``) and from
nowhere else — no URL, no view, no API calls it, and none may: an erase is
irreversible, and the only thing standing between a stolen session and a
wiped colleague would be a button.

What "as if they never existed" can and cannot mean here, said plainly:

* **Erased:** the account, its person, their community and circle
  memberships, their files and buckets (the bytes on this disk too), their
  wiki revisions — and the wiki pages only they ever wrote — their
  subscriptions, grants, sessions, tokens, and everything else that cascades
  from the account (Django's own collector decides; the report lists it).
* **Kept, detached:** rows other people still need keep their place and lose
  the pointer — a workflow they started, a ledger account (its history is
  the platform's money trail), a forum room they opened.
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
    """What erasing ``user`` does — nothing is written."""
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
    empty_pages = _pages_left_empty(user)
    if empty_pages:
        deleted["wakawaka.WikiPage"] += len(empty_pages)
    notes = []
    vault_files = [f for m, objs in collector.data.items() if _label(m) == "vault.VaultFile"
                   for f in objs]
    remote = [f for f in vault_files if getattr(getattr(f, "bucket", None), "storage_backend", "local")
              not in ("", "local")]
    if remote:
        notes.append(f"{len(remote)} file(s) live in a remote bucket: their rows go, the remote "
                     "objects do not — remove them at the remote store.")
    if deleted.get("gitea.GiteaAccount"):
        notes.append("The forge (Gitea) keeps its own account for them: remove it in Gitea's "
                     "site administration.")
    notes.append("The audit chain keeps their records, by username, and records this erasure: "
                 "a sealed record cannot be removed without breaking every later one.")
    notes.append("Backups taken before now still hold them.")
    return {"username": user.get_username(), "id": user.pk,
            "superuser": bool(user.is_superuser),
            "deleted": dict(sorted(deleted.items())),
            "detached": dict(sorted(detached.items())),
            "blocked_by": blockers, "notes": notes}


def _pages_left_empty(user) -> list[int]:
    """Wiki pages whose every revision is theirs: with the revisions gone the
    page would be an empty shell nothing can open, so it goes with them."""
    if not apps.is_installed("wakawaka"):
        return []
    from django.db.models import Count, F, Q

    from wakawaka.models import WikiPage

    return list(WikiPage.objects.annotate(
        total=Count("revisions"), theirs=Count("revisions", filter=Q(revisions__creator=user)))
        .filter(total__gt=0, total=F("theirs")).values_list("pk", flat=True))


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
        pk = user.pk
        with transaction.atomic():
            empty_pages = _pages_left_empty(user)
            user.delete()
            if empty_pages:
                from wakawaka.models import WikiPage

                WikiPage.objects.filter(pk__in=empty_pages).delete()
        self._record(username, pk, report)
        self.emit({"ok": True, "erased": True, "report": report})

    def _record(self, username, pk, report) -> None:
        if not apps.is_installed("toto.audit"):
            return
        from toto.audit.services import SYSTEM, record

        try:
            with transaction.atomic():
                record("auth.account_erased", app_label="auth", object_type="auth.user",
                       object_id=str(pk), description=username, actor_user=SYSTEM,
                       source="console",
                       metadata={"deleted": report["deleted"], "detached": report["detached"]})
        except Exception:  # noqa: BLE001 - the erase has happened; say so, do not undo it
            self.stderr.write("the erase is done, but the audit record could not be written")
