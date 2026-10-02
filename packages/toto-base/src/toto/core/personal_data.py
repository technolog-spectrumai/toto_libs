"""A copy of one member's data, as one zip (2026-10-01, RODO art. 15 and 20).

Two doors build it and both call :func:`write_zip`: the console's
``manage.py export_user`` (``deploy.py <config> export-user``) and the
member's own *Download my data* on My account, which queues the same export
into their personal bucket (``toto.socialhub.data_export``). One builder, so
the two copies cannot drift apart.

What goes in, each as a CSV and a JSON file and named in the README.txt the
zip carries: the account and the profile (the avatar too), the communities
and clearances held, membership applications made with their address, the
privacy notices accepted, the subscription and its charges, the ledger
accounts and a statement of every entry on them, events owned, organised and
invited to (with the answer given), availability, forum messages they sent,
their sign-in sessions, the audit records whose actor is them and, apart,
those about them that somebody else wrote (without that side's address and
browser — :func:`_about_you`) — and their own vault files, the bytes in
folders by bucket, with an index.

What never goes in: a password hash, a key, a token, a session key, a sealed
or encrypted body, a verification code (:data:`SECRET_FIELD`). Vault files
are the ones they OWN and may still read under the bucket clearances
(``vault.access.gate_by_bucket`` — pessimistic, no owner bypass); an
encrypted or remote file is listed in the index without its bytes, and the
index says why. Earlier exports in their bucket are left out, or every copy
would carry all the copies before it.

Apps the library cannot import — a host's own apps — add tables through a
``PersonalDataPlugin`` in ``<app>/plugins/personal_data_plugins.py``, found
by ``autodiscover_plugins`` the first time an export is built; a section of
an app that is not installed is simply absent, and the README says only what
is there.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

from django.apps import apps
from django.db import models
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from toto.core.plugin import BasePlugin

#: Field names never exported, whatever the model: credentials and anything
#: sealed. A name match, so a new ``*_token`` column is left out before
#: anybody remembers to list it.
SECRET_FIELD = re.compile(
    r"password|secret|token|cipher|sealed|salt|nonce|signature|fingerprint|credential"
    r"|private|session_key|idempotency_key|dedupe_key|(^|_)hash$|^key_|_key_id$")

#: Where the vault files go inside the zip.
FILES_DIR = "files"


@dataclass
class Table:
    """One CSV and one JSON file of the export."""

    name: str
    about: str
    rows: list = field(default_factory=list)


class PersonalDataPlugin(BasePlugin):
    """An app's tables for one member's export — for apps the library must
    not import (module docstring). ``tables(user)`` returns a list of
    :class:`Table`; only the member's own rows, and nothing
    :data:`SECRET_FIELD` would drop."""

    registry: ClassVar[dict] = {}

    def tables(self, user) -> list[Table]:
        raise NotImplementedError


_discovered = False


def plugins() -> list[PersonalDataPlugin]:
    global _discovered
    if not _discovered:
        from toto.core.plugin_autodiscover import autodiscover_plugins

        autodiscover_plugins("plugins.personal_data_plugins")
        _discovered = True
    return PersonalDataPlugin.all()


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

def plain(value):
    """A value as JSON and CSV can both hold it."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    if hasattr(value, "name") and hasattr(value, "storage"):    # a FieldFile
        return value.name or ""
    if isinstance(value, (dict, list, tuple)):
        return json.loads(json.dumps(value, default=str))
    return str(value)


def row_of(obj, *, omit=()) -> dict:
    """Every concrete column of ``obj`` but the secret ones and ``omit``;
    a foreign key as its id (``owner_id``)."""
    row = {}
    for f in obj._meta.concrete_fields:
        if f.name in omit or SECRET_FIELD.search(f.name) or isinstance(f, models.BinaryField):
            continue
        row[f.attname] = plain(f.value_from_object(obj))
    return row


def rows_of(queryset, *, omit=()) -> list[dict]:
    return [row_of(obj, omit=omit) for obj in queryset]


# ---------------------------------------------------------------------------
# The library's own tables
# ---------------------------------------------------------------------------

def _person(user):
    from toto.people.models import Person

    return Person.objects.filter(user=user).first()


def _account(user, person) -> list[Table]:
    account = {name: plain(getattr(user, name, None)) for name in (
        "id", "username", "first_name", "last_name", "email", "date_joined", "last_login",
        "is_active", "is_staff", "is_superuser")}
    tables = [Table("account", _("Your account: name, e-mail address, when it was made and "
                                 "last used. Never the password."), [account])]
    if person is None:
        return tables
    tables.append(Table("profile", _("Your profile as other members see it, and your "
                                     "settings (time zone, language, location sharing)."),
                        [row_of(person)]))
    if person.address_id:
        tables.append(Table("address", _("The address on your profile."),
                            [row_of(person.address)]))
    tables.append(Table("communities", _("The communities you belong to."),
                        list(person.communities.order_by("name").values("id", "name", "slug"))))
    tables.append(Table("clearances", _("The clearances you hold: what you are trusted to read."),
                        list(person.clearances.order_by("name").values("id", "name", "slug"))))
    return tables


def _socialhub(user, person) -> list[Table]:
    from toto.socialhub.models import MembershipApplication, PrivacyAcceptance

    tables = []
    if user.email:
        tables.append(Table(
            "applications", _("Membership applications made with your e-mail address."),
            [{**row, "community": plain(name)} for row, name in (
                (row_of(a, omit=("code",)), a.community.name)
                for a in MembershipApplication.objects.filter(email__iexact=user.email)
                .select_related("community").order_by("created_at"))]))
    if person is not None:
        tables.append(Table(
            "privacy_acceptances", _("The versions of the privacy notice you accepted, and when."),
            rows_of(PrivacyAcceptance.objects.filter(person=person).order_by("version"))))
    return tables


def _subscriptions(user) -> list[Table]:
    if not apps.is_installed("toto.subscriptions"):
        return []
    from toto.subscriptions.models import Subscription, SubscriptionCharge

    return [
        Table("subscription", _("Your plan and its state."),
              rows_of(Subscription.objects.filter(user=user))),
        Table("charges", _("Every charge for your plan, settled or not."),
              rows_of(SubscriptionCharge.objects.filter(subscription__user=user)
                      .order_by("created_at"))),
    ]


def _ledger(user) -> list[Table]:
    if not apps.is_installed("toto.assets"):
        return []
    from toto.assets.models import LedgerAccount, LedgerEntry

    entries = (LedgerEntry.objects.filter(account__user=user)
               .select_related("account", "asset", "transaction").order_by("created_at", "pk"))
    return [
        Table("ledger_accounts", _("Your accounts on the platform's ledger."),
              rows_of(LedgerAccount.objects.filter(user=user).order_by("code"))),
        Table("ledger_statement", _("Every entry on your ledger accounts: a statement. "
                                    "Positive amounts came in, negative ones went out."),
              [{"date": plain(e.created_at), "account": e.account.code,
                "reference": e.transaction.reference,
                "type": e.transaction.transaction_type,
                "description": e.transaction.description,
                "asset": e.asset.unit_name, "amount": plain(e.amount_display)}
               for e in entries]),
    ]


def _events(person) -> list[Table]:
    if person is None or not apps.is_installed("toto.events"):
        return []
    from django.db.models import Q

    from toto.events.models import Availability, EventInvite, ScheduledEvent

    return [
        Table("events", _("Events you created or organise."),
              rows_of(ScheduledEvent.objects.filter(Q(owner=person) | Q(organizers=person))
                      .distinct().order_by("start_time"))),
        Table("event_invitations", _("Events you were invited to, and your answer."),
              [{**row_of(i), "event": i.event.title} for i in
               EventInvite.objects.filter(person=person).select_related("event")
               .order_by("sent_at")]),
        Table("availability", _("The times you marked yourself available or busy."),
              rows_of(Availability.objects.filter(person=person).order_by("start_time"))),
    ]


def _forum(user) -> list[Table]:
    if not apps.is_installed("toto.forum"):
        return []
    from toto.forum.models import ForumMessage

    return [Table(
        "forum_messages", _("The messages you sent in forum rooms. In an encrypted room "
                            "the text is sealed and is not included."),
        [{**row_of(m, omit=("sender_avatar_url",)), "room": m.channel.name}
         for m in ForumMessage.objects.filter(sender=user).select_related("channel")
         .order_by("created_at")])]


def _sessions(user) -> list[Table]:
    from toto.core.models import UserSession

    return [Table("sessions", _("Where you are signed in: browser or desktop, address and "
                                "when. Never the session key."),
                  rows_of(UserSession.objects.filter(user=user).order_by("created_at")))]


#: The chain's own columns: they say nothing about the member.
CHAIN_COLUMNS = ("chain", "previous_hash", "record_hash", "algorithm")

#: Metadata keys that hold an address or a browser — the address a sign-in
#: pause names is the guesser's (``AUTH.LOCKED``).
OTHER_SIDE_KEYS = frozenset({"address", "ip", "ip_address", "user_agent"})


def _about_you(record) -> dict:
    """A record about the member that somebody else wrote, without that
    side's address and browser (2026-10-01): ``request_source`` is theirs,
    and so is the address a pause names. A copy of one's data must not give
    away somebody else's (RODO art. 15(4)). Who it was stays — their
    username: who changed the member's account is the member's to know."""
    row = row_of(record, omit=(*CHAIN_COLUMNS, "request_source"))
    if isinstance(row.get("metadata"), dict):
        row["metadata"] = {key: value for key, value in row["metadata"].items()
                           if key not in OTHER_SIDE_KEYS}
    return row


def _subjects(user, person) -> list:
    """What else on the chain is the member's, as ``records_about`` takes it:
    their profile, the applications made with their address and the
    references asked for those."""
    from toto.socialhub.models import MembershipApplication, ReferenceRequest

    subjects = []
    if person is not None:
        subjects.append(("people.person", [person.pk]))
    if user.email:
        applications = list(MembershipApplication.objects.filter(email__iexact=user.email)
                            .values_list("pk", flat=True))
        subjects.append(("socialhub.membershipapplication", applications))
        subjects.append(("socialhub.referencerequest", list(
            ReferenceRequest.objects.filter(application_id__in=applications)
            .values_list("pk", flat=True))))
    return subjects


def _audit(user, person) -> list[Table]:
    if not apps.is_installed("toto.audit"):
        return []
    from toto.audit.models import AuditRecord
    from toto.audit.queries import records_about

    # Two tables, which the README tells apart (2026-10-01): what the member
    # did, with the address and browser they did it from, and what others
    # did about them, without the others' (_about_you).
    return [
        Table("audit_records", _("What the platform's audit trail records you doing: "
                                 "sign-ins, changes, requests."),
              rows_of(AuditRecord.objects.filter(actor_user=user)
                      .order_by("timestamp", "sequence"), omit=CHAIN_COLUMNS)),
        Table("audit_records_about_you", _(
            "What the audit trail records others doing about you: an administrator "
            "changing your account, sign-ins tried with your name and the pauses that "
            "followed, communities and clearances given or taken, your applications and "
            "requests handled. Their address and browser are left out: those are theirs."),
              [_about_you(r) for r in records_about(user, also=_subjects(user, person))]),
    ]


def tables_for(user) -> list[Table]:
    """Every table of ``user``'s export, the plugins' last."""
    person = _person(user)
    tables = [*_account(user, person), *_socialhub(user, person), *_subscriptions(user),
              *_ledger(user), *_events(person), *_forum(user), *_sessions(user),
              *_audit(user, person)]
    for plugin in plugins():
        tables.extend(plugin.tables(user))
    return tables


# ---------------------------------------------------------------------------
# Vault files
# ---------------------------------------------------------------------------

def _safe(part: str) -> str:
    part = (part or "").replace("\\", "/")
    return "/".join(p for p in part.split("/") if p not in ("", ".", "..")) or "_"


def vault_files(user, *, exclude_ids=()):
    """``(index rows, [(arcname, VaultFile)])``: the files ``user`` owns and
    may read, by bucket; the bytes only of local, unencrypted ones."""
    if not apps.is_installed("toto.vault"):
        return [], []
    from toto.vault import access
    from toto.vault.models import VaultFile

    files = (access.gate_by_bucket(user, VaultFile.objects.filter(owner=user))
             .exclude(pk__in=list(exclude_ids)).select_related("bucket", "directory")
             .order_by("bucket__slug", "pk"))
    rows, picked, seen = [], [], set()
    for f in files:
        bucket = f.bucket.slug if f.bucket_id else "no-bucket"
        folder = f.directory.full_path() if f.directory_id else ""
        path, note = "", ""
        if f.is_encrypted:
            note = _("encrypted: decrypt it in the vault and download it there")
        elif not access.is_local_content(f):
            note = _("kept in a remote bucket: download it from the vault")
        elif not f.file:
            note = _("no content")
        else:
            name = os.path.basename(_safe(f.title or f.key or f"file-{f.pk}"))
            path = "/".join(p for p in (FILES_DIR, _safe(bucket), _safe(folder) if folder else "",
                                        name) if p)
            stem, ext = os.path.splitext(path)
            n = 2
            while path in seen:
                path = f"{stem}-{n}{ext}"
                n += 1
            seen.add(path)
            picked.append((path, f))
        rows.append({"id": f.pk, "title": f.title, "bucket": bucket, "folder": folder,
                     "type": f.file_type, "size_bytes": f.file_size_bytes,
                     "uploaded_at": plain(f.uploaded_at), "public": f.is_public,
                     "encrypted": f.is_encrypted, "path_in_zip": path, "note": note})
    return rows, picked


def _earlier_exports(user) -> list[int]:
    if not apps.is_installed("toto.socialhub"):
        return []
    from toto.socialhub.models import DataExport

    return list(DataExport.objects.filter(user=user, output__isnull=False)
                .values_list("output_id", flat=True))


# ---------------------------------------------------------------------------
# The zip
# ---------------------------------------------------------------------------

def _csv(rows: list[dict]) -> str:
    out = io.StringIO()
    columns = []
    for row in rows:
        columns.extend(k for k in row if k not in columns)
    writer = csv.DictWriter(out, fieldnames=columns or ["(empty)"])
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list))
                         else v for k, v in row.items()})
    return out.getvalue()


def _readme(user, tables, file_rows, made_at) -> str:
    lines = [
        _("A copy of your data on this platform"),
        "",
        _("Account: %(username)s. Made: %(when)s.") % {
            "username": user.get_username(), "when": made_at.isoformat(timespec="seconds")},
        "",
        _("Every table comes twice: as a .csv file (opens in a spreadsheet) and as a "
          ".json file (for programs). Times are in UTC."),
        _("Left out on purpose: your password, keys, tokens, session keys and anything "
          "sealed or encrypted."),
        "",
    ]
    for t in tables:
        lines.append(f"{t.name}.csv / {t.name}.json ({len(t.rows)}): {t.about}")
    lines += [
        "",
        f"{FILES_DIR}/index.csv / {FILES_DIR}/index.json ({len(file_rows)}): " + _(
            "Your own files in the vault, the ones you may still open, in folders by "
            "bucket under %(dir)s/. A file whose bytes are not here says why in the "
            "\"note\" column.") % {"dir": FILES_DIR},
        "",
        _("Not here: other members' messages and files, and copies outside this "
          "platform's database (backups, files you shared elsewhere)."),
        "",
    ]
    return "\n".join(lines)


def _language(user) -> str:
    person = _person(user)
    return (getattr(person, "preferred_language", "") or "") or translation.get_language() or "en"


def write_zip(user, fileobj) -> dict:
    """Write ``user``'s export into ``fileobj`` (seekable or not) and return
    a summary: the rows per table and the files included."""
    made_at = timezone.now()
    with translation.override(_language(user)):
        tables = tables_for(user)
        file_rows, picked = vault_files(user, exclude_ids=_earlier_exports(user))
        readme = _readme(user, tables, file_rows, made_at)
    with zipfile.ZipFile(fileobj, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", readme)
        for t in tables:
            zf.writestr(f"{t.name}.csv", _csv(t.rows))
            zf.writestr(f"{t.name}.json", json.dumps(t.rows, ensure_ascii=False, indent=1))
        zf.writestr(f"{FILES_DIR}/index.csv", _csv(file_rows))
        zf.writestr(f"{FILES_DIR}/index.json", json.dumps(file_rows, ensure_ascii=False, indent=1))
        person = _person(user)
        if person is not None and person.avatar:
            try:
                with person.avatar.open("rb") as src:
                    zf.writestr(f"profile_avatar{os.path.splitext(person.avatar.name)[1]}",
                                src.read())
            except OSError:
                pass
        written = 0
        for arcname, f in picked:
            try:
                with f.file.open("rb") as src, zf.open(arcname, "w") as dest:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        dest.write(chunk)
                written += 1
            except OSError:
                continue        # a missing blob on disk: the index still lists it
    return {"tables": {t.name: len(t.rows) for t in tables}, "files": written,
            "files_listed": len(file_rows)}
