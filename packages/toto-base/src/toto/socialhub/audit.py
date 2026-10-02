"""Communities, clearances and who is in them, on the audit chain (2026-09-28).

Signals, not view patches, so every writer is covered — the admin, the
membership flow, the Clearances tab, the ingress, a shell:

| action | when |
|---|---|
| `SOCIALHUB.COMMUNITY_CREATED` / `_CHANGED` / `_DELETED` | a community is made, edited (the fields that change, before and after), removed |
| `SOCIALHUB.MEMBER_ADDED` / `_REMOVED` | a person joins or leaves a community — `Person.communities`, from either side, a `clear()` included |
| `SOCIALHUB.CLEARANCE_CREATED` / `_CHANGED` / `_DELETED` | a clearance is made, edited (name, slug, the refill speeds), removed — with a `CLEARANCE_MEMBER_REMOVED` for everybody who held it, and their slugs in `holders` (2026-09-29) |
| `SOCIALHUB.CLEARANCE_MEMBER_ADDED` / `_REMOVED` | a person is given or loses a clearance — `Person.clearances`, from either side, a `clear()` included |
| `SOCIALHUB.SENIOR_ADDED` / `_REMOVED` | a senior member named or dropped |
| `SOCIALHUB.PRIVILEGE_CHANGED` / `_REMOVED` | a community's grants (`may_*`) set or cleared |
| `SOCIALHUB.APPLICATION_SUBMITTED` | somebody applies to join a community — every application and reference record names the application by its id and its community, never the applicant's e-mail address (2026-10-01, 37c.32; the sealed records before it keep theirs) |
| `SOCIALHUB.APPLICATION_<STATUS>` | the application moves: verified, endorsed, invited, rejected |
| `SOCIALHUB.APPLICATION_RENEWED` | somebody applies again with the address of an application that lapsed before its applicant got in: a new code and a new week, the community chosen now (2026-10-01) |
| `SOCIALHUB.REFERENCE_REQUESTED` / `_GIVEN` / `_DECLINED` | a reference asked of a member, and their answer (given = the applicant admitted) |
| `SOCIALHUB.PROFILE_CHANGED` | a member edits their own profile or time zone on My account — the field NAMES in `fields`, never the values (2026-09-30) |
| `PRIVACY.NOTICE_ACCEPTED` | an applicant ticks the privacy notice on the membership application — the version and the application, by its id as every application record names it (2026-10-01) |
| `PRIVACY.EXPORT_REQUESTED` | a member asks for a copy of their data on My account (2026-10-01) |
| `PRIVACY.EXPORT_READY` / `_FAILED` | the copy is in their bucket — the rows per table, the files and the vault file's id — or could not be made; the system's, not the member's (2026-10-01) |
| `PRIVACY.ERASURE_REQUESTED` | a member files a request to have their account erased on My account (2026-10-01) |
| `PRIVACY.ERASURE_DONE` / `_DECLINED` | the console's `erase_user` erased them and closed the request (the system's), or a superuser on the plan declined it — with the note's length, not the note (2026-10-01) |
| `PRIVACY.NOTICE_PUBLISHED` | a new version of the privacy notice is published — its number, the one it replaces, each text's length and whether the platform seeded it (37c.32), never the text (2026-10-01) |

Communities and clearances are orthogonal on purpose (README) and are
recorded apart, so the chain shows which axis a change touched. The actor is whoever is at the keyboard (the audit context); nothing is
recorded where ``toto.audit`` is not installed, and a record that cannot be
written never fails the change it describes.
"""

from __future__ import annotations

import logging

from django.apps import apps
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete, pre_save

log = logging.getLogger("toto.socialhub")

APP_LABEL = "socialhub"

#: The community fields whose change is worth a record.
TRACKED = ("name", "slug", "org_type", "parent_id", "head_id")
#: The clearance fields whose change is worth a record.
TRACKED_CLEARANCE = ("name", "slug", "regen_security", "regen_compute", "regen_storage")


def installed() -> bool:
    return apps.is_installed("toto.audit")


def _record(action, *, object_type, object_id, description, family="socialhub", **kwargs):
    if not installed():
        return None
    from django.db import transaction

    from toto.audit.services import record

    try:
        with transaction.atomic():      # a failed insert must not poison the caller's
            return record(f"{family}.{action}", app_label=APP_LABEL,
                          object_type=object_type, object_id=str(object_id),
                          description=str(description)[:500], **kwargs)
    except Exception:  # noqa: BLE001 - the chain never breaks a membership change
        log.exception("audit: could not record %s.%s", family, action)
        return None


def _community(community, action, **metadata):
    return _record(action, object_type="socialhub.community", object_id=community.pk,
                   description=community.name,
                   metadata={"community": community.slug, "name": community.name, **metadata})


def _clearance(clearance, action, **metadata):
    return _record(action, object_type="socialhub.clearance", object_id=clearance.pk,
                   description=clearance.name,
                   metadata={"clearance": clearance.slug, "name": clearance.name, **metadata})


def _person(person) -> dict:
    return {"person": person.slug, "display_name": person.display_name,
            "user": person.user_id}


def profile_changed(person, fields) -> None:
    """A member changed their own profile on My account (2026-09-30).

    Called by the view, not a signal: a Person is saved from a dozen places
    (the map pin, the language, the sync) and only this door is "the member
    edited their profile". The field names and nothing else — a bio or a
    phone number is the member's to show, not the chain's to keep.
    """
    fields = sorted(fields)
    if not fields:
        return None
    return _record("profile_changed", object_type="people.person", object_id=person.pk,
                   description=person.display_name, changes={},
                   metadata={"person": person.slug, "fields": fields})


def notice_published(notice, *, previous=None) -> None:
    """A new version of the privacy notice (2026-10-01). Its own family,
    ``PRIVACY.*``, so the data-protection trail reads apart from the
    membership one. The lengths show a change happened; the text itself is
    public on the notice's own page and has no place on the chain. ``seeded``
    says the platform published it from the host's files (37c.32)."""
    return _record("notice_published", family="privacy", object_type="socialhub.privacynotice",
                   object_id=notice.pk, description=f"v{notice.version}",
                   metadata={"version": notice.version, "previous": previous,
                             "length_pl": len(notice.text_pl),
                             "length_en": len(notice.text_en),
                             "seeded": bool(getattr(notice, "seeded", False))})


def notice_accepted(application) -> None:
    """An applicant accepted a version of the privacy notice (2026-10-01).
    Called by the application view, the one door an acceptance comes
    through; the Person it is carried to on admission is the socialhub's
    ``PrivacyAcceptance`` row, not a second record."""
    return _record("notice_accepted", family="privacy",
                   object_type="socialhub.membershipapplication", object_id=application.pk,
                   description=f"v{application.privacy_version} application {application.pk}",
                   metadata={**_application_facts(application),
                             "version": application.privacy_version})


def _export(export, action, **kwargs):
    user = export.user
    return _record(action, family="privacy", object_type="socialhub.dataexport",
                   object_id=export.pk, description=user.get_username(), **kwargs)


def export_requested(export, *, request=None) -> None:
    """A member asked for a copy of their data (2026-10-01)."""
    return _export(export, "export_requested", actor_user=export.user, request=request,
                   metadata={"user": export.user_id})


def export_ready(export, *, source="worker") -> None:
    """The copy is filed in the member's bucket. Counts and the file's id —
    what the zip holds is the member's, not the chain's."""
    if not installed():
        return None
    from toto.audit.services import SYSTEM

    return _export(export, "export_ready", actor_user=SYSTEM, source=source,
                   metadata={"user": export.user_id, "vault_file": export.output_id,
                             "tables": export.summary.get("tables", {}),
                             "files": export.summary.get("files", 0)})


def export_failed(export) -> None:
    if not installed():
        return None
    from toto.audit.services import SYSTEM

    return _export(export, "export_failed", actor_user=SYSTEM, source="worker", success=False,
                   metadata={"user": export.user_id, "reason": export.error})


def _erasure(ticket, action, **kwargs):
    return _record(action, family="privacy", object_type="socialhub.erasurerequest",
                   object_id=ticket.pk, description=ticket.username, **kwargs)


def erasure_requested(ticket, *, request=None) -> None:
    """A member asked to be erased (2026-10-01). The username is the
    description, as on every record of theirs: the chain keeps it after the
    erase, which is what the request's confirmation tells them."""
    return _erasure(ticket, "erasure_requested", actor_user=ticket.user, request=request,
                    metadata={"user": ticket.user_id})


def erasure_done(ticket) -> None:
    """The console erased them and closed the request — the system's record;
    ``AUTH.ACCOUNT_ERASED`` beside it is the erase itself."""
    if not installed():
        return None
    from toto.audit.services import SYSTEM

    return _erasure(ticket, "erasure_done", actor_user=SYSTEM, source="console",
                    metadata={"user_was": ticket.username})


def erasure_declined(ticket, *, request=None) -> None:
    """A superuser declined it. The note is for the member and stays on the
    ticket; the chain has that there was one."""
    return _erasure(ticket, "erasure_declined", actor_user=ticket.handled_by, request=request,
                    metadata={"user": ticket.user_id, "note_length": len(ticket.note)})


# ---------------------------------------------------------------------------
# Communities
# ---------------------------------------------------------------------------


def _community_before(sender, instance, **kwargs):
    instance._audit_before = (type(instance).objects.filter(pk=instance.pk).values(*TRACKED).first()
                              if instance.pk else None)


def _community_after(sender, instance, created, **kwargs):
    if created:
        _community(instance, "community_created", org_type=instance.org_type,
                   parent=instance.parent_id)
        return
    before = getattr(instance, "_audit_before", None)
    instance._audit_before = None
    if not before:
        return
    changed = {field for field in TRACKED if before[field] != getattr(instance, field)}
    if not changed:
        return
    _community(instance, "community_changed",
               changed=sorted(changed),
               before={f: str(before[f]) if before[f] is not None else None for f in sorted(changed)},
               after={f: (str(getattr(instance, f)) if getattr(instance, f) is not None else None)
                      for f in sorted(changed)})


def _community_deleted(sender, instance, **kwargs):
    _community(instance, "community_deleted")


# ---------------------------------------------------------------------------
# Clearances (2026-09-29) — the same shape, on their own model
# ---------------------------------------------------------------------------


def _clearance_before(sender, instance, **kwargs):
    instance._audit_before = (type(instance).objects.filter(pk=instance.pk)
                              .values(*TRACKED_CLEARANCE).first() if instance.pk else None)


def _clearance_after(sender, instance, created, **kwargs):
    if created:
        _clearance(instance, "clearance_created", speeds={k: str(v) for k, v in instance.regen_speeds().items()})
        return
    before = getattr(instance, "_audit_before", None)
    instance._audit_before = None
    if not before:
        return
    changed = {field for field in TRACKED_CLEARANCE if before[field] != getattr(instance, field)}
    if not changed:
        return
    _clearance(instance, "clearance_changed",
               changed=sorted(changed),
               before={f: str(before[f]) if before[f] is not None else None for f in sorted(changed)},
               after={f: (str(getattr(instance, f)) if getattr(instance, f) is not None else None)
                      for f in sorted(changed)})


def _clearance_leaving(sender, instance, **kwargs):
    # The delete cascades Person.clearances rows without m2m_changed, so the
    # holders are read here, before they go, and recorded after.
    instance._audit_holders = list(instance.members.all())


def _clearance_deleted(sender, instance, **kwargs):
    holders = getattr(instance, "_audit_holders", None) or []
    instance._audit_holders = None
    for person in holders:
        _clearance(instance, "clearance_member_removed", **_person(person))
    _clearance(instance, "clearance_deleted", holders=[person.slug for person in holders])


# ---------------------------------------------------------------------------
# Who is in them — both sides of one list
# ---------------------------------------------------------------------------


def _membership_handler(added: str, removed: str, *, forward_manager: str, reverse_manager: str,
                        group: str = "Community", recorder=None):
    """An m2m_changed receiver for a Person ↔ Community (or Clearance) list.

    ``add`` reports only the rows it created (Django filters the ones already
    there); ``remove`` is given whatever the caller passed, so the members it
    really removes are read first; ``clear`` names nobody, so they are read
    before it runs. One record per (person, community) change.
    """

    def handler(sender, instance, action, reverse, model, pk_set, **kwargs):
        from toto.people.models import Person

        from . import models as socialhub_models

        Group = getattr(socialhub_models, group)
        record_for = recorder or _community

        if action in ("pre_remove", "pre_clear"):
            manager = getattr(instance, reverse_manager if reverse else forward_manager)
            current = set(manager.values_list("pk", flat=True))
            instance._audit_leaving = current & set(pk_set) if action == "pre_remove" else current
            return
        if action == "post_add":
            pks, what = set(pk_set or ()), added
        elif action in ("post_remove", "post_clear"):
            pks, what = getattr(instance, "_audit_leaving", set()), removed
            instance._audit_leaving = set()
        else:
            return
        if not pks:
            return
        if reverse:           # instance is the group; the pks are people
            for person in Person.objects.filter(pk__in=pks):
                record_for(instance, what, **_person(person))
        else:                 # instance is a Person; the pks are groups
            for row in Group.objects.filter(pk__in=pks):
                record_for(row, what, **_person(instance))

    return handler


_members = _membership_handler("member_added", "member_removed",
                               forward_manager="communities", reverse_manager="members")
_holders = _membership_handler("clearance_member_added", "clearance_member_removed",
                               forward_manager="clearances", reverse_manager="members",
                               group="Clearance", recorder=_clearance)
_seniors = _membership_handler("senior_added", "senior_removed",
                               forward_manager="senior_communities",
                               reverse_manager="senior_members")


def _seniors_changed(sender, instance, action, reverse, model, pk_set, **kwargs):
    # senior_members is declared on Community, so its "forward" side is the
    # community: the generic handler's sides are Person-first, hence the flip.
    return _seniors(sender, instance, action, not reverse, model, pk_set, **kwargs)


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------


def _grants(privilege) -> dict:
    return {f.name: bool(getattr(privilege, f.name))
            for f in privilege._meta.concrete_fields if f.name.startswith("may_")}


def _privilege_saved(sender, instance, created, **kwargs):
    _community(instance.community, "privilege_changed", grants=_grants(instance))


def _privilege_deleted(sender, instance, **kwargs):
    from .models import Community

    community = Community.objects.filter(pk=instance.community_id).first()
    if community is not None:
        _community(community, "privilege_removed")


# ---------------------------------------------------------------------------
# Applications and references
# ---------------------------------------------------------------------------


def _status_before(sender, instance, **kwargs):
    instance._audit_status = (type(instance).objects.filter(pk=instance.pk)
                              .values_list("status", flat=True).first() if instance.pk else None)


def _application_facts(application) -> dict:
    """What an application's records say of it: its id and its community —
    never the applicant's e-mail address (2026-10-01, 37c.32). The chain is
    sealed and outlives the application: an address written on it stayed
    after the housekeeping pruned the lapsed application, and after an erase.
    The id finds the row while it lives (``records_about`` is handed the
    member's application ids), and nothing once it is gone. Records written
    before keep their address: a sealed record is never rewritten."""
    community = application.community
    return {"application": application.pk, "community": community.slug,
            "name": community.name}


def _application_label(application) -> str:
    return f"application {application.pk}"


def _application_saved(sender, instance, created, **kwargs):
    before = getattr(instance, "_audit_status", None)
    if created:
        action = "application_submitted"
    elif getattr(instance, "_audit_renewed", False):
        # Applied again after it lapsed (applications.renew, 2026-10-01): one
        # record saying so, not a move back to "pending".
        instance._audit_renewed = False
        action = "application_renewed"
    elif before is not None and before != instance.status:
        action = f"application_{instance.status}"
    else:
        return
    _record(action, object_type="socialhub.membershipapplication", object_id=instance.pk,
            description=_application_label(instance),
            metadata={**_application_facts(instance), "before": before,
                      "status": instance.status})


def _reference_saved(sender, instance, created, **kwargs):
    before = getattr(instance, "_audit_status", None)
    if created:
        action = "reference_requested"
    elif before != instance.status and instance.status == "accepted":
        action = "reference_given"
    elif before != instance.status and instance.status == "declined":
        action = "reference_declined"
    else:
        return
    _record(action, object_type="socialhub.referencerequest", object_id=instance.pk,
            description=_application_label(instance.application),
            metadata={**_application_facts(instance.application),
                      "referrer": instance.referrer.slug,
                      "referrer_name": instance.referrer.display_name,
                      "admitted": instance.status == "accepted"})


def connect() -> None:
    from toto.people.models import Person

    from .models import Clearance, Community, CommunityPrivilege, MembershipApplication, ReferenceRequest

    uid = "toto.socialhub.audit."
    pre_save.connect(_community_before, sender=Community, weak=False, dispatch_uid=uid + "c_before")
    post_save.connect(_community_after, sender=Community, weak=False, dispatch_uid=uid + "c_after")
    post_delete.connect(_community_deleted, sender=Community, weak=False,
                        dispatch_uid=uid + "c_deleted")
    m2m_changed.connect(_members, sender=Person.communities.through, weak=False,
                        dispatch_uid=uid + "members")
    pre_save.connect(_clearance_before, sender=Clearance, weak=False, dispatch_uid=uid + "cl_before")
    post_save.connect(_clearance_after, sender=Clearance, weak=False, dispatch_uid=uid + "cl_after")
    pre_delete.connect(_clearance_leaving, sender=Clearance, weak=False,
                       dispatch_uid=uid + "cl_leaving")
    post_delete.connect(_clearance_deleted, sender=Clearance, weak=False,
                        dispatch_uid=uid + "cl_deleted")
    m2m_changed.connect(_holders, sender=Person.clearances.through, weak=False,
                        dispatch_uid=uid + "holders")
    m2m_changed.connect(_seniors_changed, sender=Community.senior_members.through, weak=False,
                        dispatch_uid=uid + "seniors")
    post_save.connect(_privilege_saved, sender=CommunityPrivilege, weak=False,
                      dispatch_uid=uid + "privilege_saved")
    post_delete.connect(_privilege_deleted, sender=CommunityPrivilege, weak=False,
                        dispatch_uid=uid + "privilege_deleted")
    for model, name in ((MembershipApplication, "application"), (ReferenceRequest, "reference")):
        pre_save.connect(_status_before, sender=model, weak=False,
                         dispatch_uid=uid + name + "_before")
    post_save.connect(_application_saved, sender=MembershipApplication, weak=False,
                      dispatch_uid=uid + "application_after")
    post_save.connect(_reference_saved, sender=ReferenceRequest, weak=False,
                      dispatch_uid=uid + "reference_after")
