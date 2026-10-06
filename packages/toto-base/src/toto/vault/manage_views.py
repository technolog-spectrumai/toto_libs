"""Storage → **Management** (``manage/``, 2026-09-30): every bucket, and the
doors that make, edit, test and delete them.

Superuser functionality: every door is a real superuser ON THE SUPERUSER PLAN
(``plan_gate.superuser_plan_door`` — the account alone is refused, 403, JSON
for the JSON doors, before anything is looked up), and the tab in
``vault/base.html`` shows to the same people (``vault_flags.superuser_plan``).

The page is a paginated list of EVERY bucket — personal ones, ownerless ones
and those being deleted included — a table on a wide screen and a card per
bucket on a narrow one, and four doors:

* **Create** (modal): a kind from the adapter registry
  (``storage_adapters.StorageAdapter.creatable_adapters`` — this server, AWS S3,
  OVH S3), its own fields (``adapter.fields()``), a name, an owner found by
  searching people (``manage_people``, JSON: any active account) and the quota
  every bucket has — and its AI shield, on a host with the assistant
  (``bucket_lifecycle.shield_offered``: without it the field is neither drawn
  nor taken from a post). The creator is whoever made it. An S3 kind
  must pass its connection test before anything is saved (``adapter.create``
  runs it). The kinds with a guided flow of their own (``GUIDED_KINDS``:
  another Zenobia's pairing code) are a card in the same dialog whose steps
  post to their own doors (``share_views``); this door refuses them.
* **Edit** (modal): the name, the owner, the quota — and the AI shield where
  the assistant is installed — nothing else, and a post naming any other
  field is refused whole (``bucket_lifecycle.update_bucket``, which records
  before and after; on a host without the assistant the shield is such a
  field).
* **Test**: one bounded connection test on a click (``adapter.probe``,
  stamped), JSON. No page render ever probes.
* **Delete** (modal): the bucket's name typed exactly; the server checks it
  again and hands the purge to a worker (``bucket_lifecycle.request_deletion``);
  the list shows the bucket as being deleted until it is gone.

A refusal is Post/Redirect/Get: what was typed and why it was refused go to
the session (``DRAFT_KEY``) and the list re-opens the modal with them —
WITHOUT a secret: no secret key, no access key id, no pairing code is ever put
in a draft, a page, a message, JSON or a log (``_carried``, ``_scrub``).

Another Zenobia (the two-sided share / connect flow, ``share_views``) plugs
into this page: ``vault/manage/_connect_steps.html`` (the New bucket dialog's
"Another Zenobia": the connect steps, inside ``_create_modal.html``),
``vault/manage/_row_share.html`` (a bucket's "Share" action) and
``vault/manage/_extra_modals.html`` (the share modal and both flows' scripts)
are its places.
"""

from __future__ import annotations

from django.apps import apps
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q, Sum
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from toto.ui import PageProcessor

from . import bucket_lifecycle
from .models import Bucket, StorageBackend, field_key_configured
from .plan_gate import superuser_plan_door
from .storage_adapters import (
    NAME_MAX,
    StorageAdapter,
    clean_name,
    clean_owner,
    clean_quota,
)

#: Buckets per page.
PER_PAGE = 20
#: People one owner search answers with.
PEOPLE_LIMIT = 20
#: Where a refused Create / Edit / Delete waits for the list to draw it.
DRAFT_KEY = "vault.manage_draft"
#: Kinds whose Create is a guided flow of its own — another Zenobia's pairing
#: code is decoded, shown, tested and only then connected — never the generic
#: form's (the generic door refuses them too). The New bucket dialog offers
#: such a kind as a card beside the others and draws its steps in place of
#: the form (``vault/manage/_connect_steps.html``).
GUIDED_KINDS = frozenset({"zenobia_remote"})
#: Inputs a draft never carries back besides each kind's secret fields: the
#: access key id is half of a credential (the list shows its last four only).
NEVER_CARRIED = frozenset({"access_key_id"})
#: What Edit posts is ``bucket_lifecycle.editable()`` (without the AI shield
#: on a host that has no assistant). Anything else in the post is refused,
#: whole.
#: What every form post carries besides its fields.
FORM_NOISE = frozenset({"csrfmiddlewaretoken"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _display_name(user) -> str:
    if user is None:
        return ""
    if apps.is_installed("toto.people"):
        try:
            name = user.community_profile.display_name
        except Exception:  # noqa: BLE001 - no Person row: the account's own name
            name = ""
        if name:
            return name
    return (user.get_full_name() or "").strip() or user.get_username()


def person_row(user) -> dict | None:
    """An account as the owner picker shows it (never an e-mail address)."""
    if user is None:
        return None
    return {"pk": user.pk, "name": _display_name(user), "username": user.get_username()}


def _owner_from(raw):
    """The account a posted ``owner`` names, or None (clean_owner then says
    which sentence applies: none chosen, or not active)."""
    raw = str(raw or "").strip()
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 18):
        return None
    return get_user_model().objects.filter(pk=int(raw)).first()


def _checked(data, name) -> bool:
    """A checkbox after its hidden ``0``: the last value wins."""
    return str(data.get(name, "") or "").strip().lower() in ("1", "on", "true", "yes")


def _shield(data) -> bool:
    """The posted AI shield — False, whatever was posted, on a host without
    the assistant (``bucket_lifecycle.shield_offered``)."""
    return bucket_lifecycle.shield_offered() and _checked(data, bucket_lifecycle.SHIELD)


def _scrub(text, secrets) -> str:
    """No secret survives into a sentence a page, a draft or JSON carries —
    a store's error may quote what it was sent."""
    text = str(text)
    for value in secrets:
        value = str(value or "")
        if len(value) >= 4:
            text = text.replace(value, "…")
    return text


def _merge(errors: dict, exc: ValidationError, secrets=()) -> None:
    """Fold a ValidationError into ``{field: [sentences]}`` (``__all__`` for
    the ones that name no field)."""
    if hasattr(exc, "error_dict"):
        items = exc.message_dict.items()
    else:
        items = [("__all__", exc.messages)]
    for name, sentences in items:
        errors.setdefault(name, []).extend(_scrub(s, secrets) for s in sentences)


def _secret_values(adapter, data) -> list:
    if adapter is None:
        return []
    names = set(adapter.secret_field_names()) | NEVER_CARRIED
    return [str(data.get(name, "") or "").strip() for name in names]


def _carried(adapter, data) -> dict:
    """The kind's fields a draft may keep: never a secret field, never a key id."""
    if adapter is None:
        return {}
    carried = {}
    for spec in adapter.fields():
        if spec.get("secret") or spec["name"] in NEVER_CARRIED:
            continue
        carried[spec["name"]] = str(data.get(spec["name"], "") or "")[: spec.get("max_length") or 200]
    return carried


def _redirect_back(request):
    url = reverse("vault:manage")
    page = str(request.POST.get("page", "") or "")
    if page.isascii() and page.isdigit() and len(page) <= 6 and page != "1":
        url += f"?page={page}"
    return redirect(url)


def generic_kinds() -> list:
    """The kinds the Create modal offers on this host, in order."""
    return [a for a in StorageAdapter.creatable_adapters() if a.get_key() not in GUIDED_KINDS]


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------

def _row(bucket) -> dict:
    adapter = StorageAdapter.for_bucket(bucket)
    if adapter is not None:
        info = adapter.describe(bucket)
        plan = adapter.destroy_plan(bucket)
        kind, kind_title, icon = adapter.get_key(), str(adapter.title), adapter.icon
    else:  # a backend no adapter knows: listed, never guessed at
        from .storage_adapters import (HEALTH_UNKNOWN, health_label, status_label,
                                       status_of)

        status = status_of(bucket)
        info = {"target": "", "status": status, "status_label": status_label(status),
                "health": HEALTH_UNKNOWN, "health_label": health_label(HEALTH_UNKNOWN),
                "detail": "", "credential": ""}
        plan = {"removes": [], "keeps": []}
        kind, kind_title, icon = bucket.storage_backend, bucket.storage_backend, "fa-solid fa-box"
    return {
        "bucket": bucket,
        "kind": kind,
        "kind_title": kind_title,
        "icon": icon,
        "info": info,
        "files": bucket.file_count or 0,
        "bytes": bucket.total_bytes or 0,
        "owner": person_row(bucket.owner) if bucket.owner_id else None,
        "creator": person_row(bucket.created_by) if bucket.created_by_id else None,
        "zenobia_remote": bucket.storage_backend == StorageBackend.REMOTE_TOTO,
        "plan": plan,
    }


def _script_row(row) -> dict:
    """What the Edit and Delete modals read for one bucket (json_script:
    names reach the page as text, never as markup)."""
    bucket = row["bucket"]
    script = {
        "pk": bucket.pk,
        "name": bucket.name,
        "owner": row["owner"],
        "storage_quota_mb": "" if bucket.storage_quota_mb is None else str(bucket.storage_quota_mb),
        "deleting": bucket.is_being_deleted,
        "zenobia_remote": row["zenobia_remote"],
        "removes": [str(s) for s in row["plan"].get("removes", [])],
        "keeps": [str(s) for s in row["plan"].get("keeps", [])],
        "edit_url": reverse("vault:manage_edit", args=[bucket.pk]),
        "delete_url": reverse("vault:manage_delete", args=[bucket.pk]),
    }
    if bucket_lifecycle.shield_offered():
        script["ai_protected"] = bool(bucket.ai_protected)
    return script


def _kinds(draft) -> list:
    """The Create modal's type picker: each kind with its fields, the draft's
    values put back (never a secret) and its errors beside them."""
    creating = draft.get("open") == "create"
    values = (draft.get("values") or {}) if creating else {}
    errors = (draft.get("errors") or {}) if creating else {}
    kinds = []
    for adapter in generic_kinds():
        key = adapter.get_key()
        mine = draft.get("kind") == key
        fields = []
        for spec in adapter.fields():
            spec = dict(spec)
            spec["value"] = "" if (spec.get("secret") or spec["name"] in NEVER_CARRIED
                                   or not mine) else values.get(spec["name"], "")
            spec["errors"] = errors.get(spec["name"], []) if mine else []
            fields.append(spec)
        blocked = ""
        if adapter.seals_secret and not field_key_configured():
            try:
                from .storage_adapters import require_field_key

                require_field_key()
            except ValidationError as exc:
                blocked = " ".join(exc.messages)
        kinds.append({
            "key": key, "title": str(adapter.title), "icon": adapter.icon,
            "summary": str(adapter.summary or ""), "is_remote": adapter.is_remote,
            "fields": fields, "blocked": blocked,
        })
    return kinds


def _empty_draft(request) -> dict:
    draft = {"open": "", "kind": "", "pk": None, "name": "",
             "owner": person_row(request.user), "storage_quota_mb": "",
             "values": {}, "errors": {}, "error": ""}
    if bucket_lifecycle.shield_offered():
        draft["ai_protected"] = False
    return draft


def _page(request, draft):
    listing = (Bucket.objects.select_related("owner", "created_by", "provider", "peer")
               .annotate(file_count=Count("files"), total_bytes=Sum("files__file_size_bytes"))
               .order_by(Lower("name"), "pk"))
    if apps.is_installed("toto.people"):
        listing = listing.select_related("owner__community_profile",
                                         "created_by__community_profile")
    page = Paginator(listing, PER_PAGE).get_page(request.GET.get("page"))
    rows = [_row(bucket) for bucket in page]
    script_rows = [_script_row(row) for row in rows]
    base = _empty_draft(request)
    if draft:
        base.update(draft)
    draft = base
    draft["me"] = person_row(request.user)
    if draft["open"] in ("edit", "delete") and draft.get("pk") not in {r["pk"] for r in script_rows}:
        # The refused bucket is on another page (or gone): its modal still opens.
        stray = listing.filter(pk=draft.get("pk")).first()
        if stray is None:
            draft["open"] = ""
        else:
            script_rows.append(_script_row(_row(stray)))
    kinds = _kinds(draft)
    if draft["open"] == "create" and draft["kind"] not in {k["key"] for k in kinds}:
        draft["kind"] = ""
    if not draft["kind"] and kinds:
        draft["kind"] = kinds[0]["key"]
    totals = Bucket.objects.aggregate(
        buckets=Count("pk"),
        deleting=Count("pk", filter=Q(deletion_requested_at__isnull=False)),
        ownerless=Count("pk", filter=Q(owner__isnull=True)),
        remote=Count("pk", filter=~Q(storage_backend__in=["", StorageBackend.LOCAL])))
    return _render(request, "vault/manage/manage.html", {
        "active_tab": "manage",
        "rows": rows,
        "script_rows": script_rows,
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "extra_query": "",
        "totals": totals,
        "kinds": kinds,
        "kinds_meta": [{"key": k["key"], "blocked": k["blocked"], "remote": bool(k["is_remote"])}
                       for k in kinds],
        "draft": draft,
        "create_errors": (draft.get("errors") or {}) if draft["open"] == "create" else {},
        "name_max": NAME_MAX,
    })


@superuser_plan_door
@require_safe
def manage(request):
    return _page(request, request.session.pop(DRAFT_KEY, None))


@superuser_plan_door(json=True)
@require_safe
def manage_people(request):
    """Accounts that may own a bucket — any ACTIVE account — by a piece of
    their name, username or e-mail; JSON for the owner picker. The answer
    names each account (name, username), never its e-mail address."""
    User = get_user_model()
    q = " ".join((request.GET.get("q") or "").split())[:80]
    if not q:
        response = JsonResponse({"people": []})
    else:
        names = {f.name for f in User._meta.get_fields()}
        match = Q(**{f"{User.USERNAME_FIELD}__icontains": q})
        for name in ("first_name", "last_name", "email"):
            if name in names:
                match |= Q(**{f"{name}__icontains": q})
        if apps.is_installed("toto.people"):
            match |= Q(community_profile__display_name__icontains=q)
        found = User.objects.filter(is_active=True).filter(match).distinct()
        if apps.is_installed("toto.people"):
            found = found.select_related("community_profile")
        found = found.order_by(User.USERNAME_FIELD)[:PEOPLE_LIMIT]
        response = JsonResponse({"people": [person_row(user) for user in found]})
    response["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@superuser_plan_door
@require_POST
def manage_create(request):
    data = request.POST
    key = str(data.get("kind", "") or "").strip()
    adapter = None if key in GUIDED_KINDS else StorageAdapter.for_key(key)
    secrets = _secret_values(adapter, data)
    owner = _owner_from(data.get("owner"))
    name = " ".join(str(data.get("name", "") or "").split())
    errors: dict = {}
    if adapter is None:
        errors["kind"] = [_("Choose what kind of bucket to make.")]
    else:
        # Every problem named at once: the common fields, then the kind's own.
        for check in (lambda: clean_name(name), lambda: clean_owner(owner),
                      lambda: clean_quota(data.get("storage_quota_mb"))):
            try:
                check()
            except ValidationError as exc:
                _merge(errors, exc, secrets)
        try:
            config, secret = adapter.validate(data)
        except ValidationError as exc:
            _merge(errors, exc, secrets)
        if not errors:
            try:
                bucket = adapter.create(
                    name, owner, request.user, config, secret,
                    storage_quota_mb=data.get("storage_quota_mb"),
                    ai_protected=_shield(data))
            except ValidationError as exc:
                _merge(errors, exc, secrets)
            except IntegrityError:
                errors["name"] = [_("A bucket with that name already exists.")]
    if errors:
        general = errors.pop("__all__", [])
        request.session[DRAFT_KEY] = {
            "open": "create", "kind": key if adapter is not None else "",
            "name": name[:NAME_MAX], "owner": person_row(owner),
            "storage_quota_mb": str(data.get("storage_quota_mb", "") or "")[:12],
            **({"ai_protected": _shield(data)} if bucket_lifecycle.shield_offered() else {}),
            "values": _carried(adapter, data),
            "errors": errors,
            "error": " ".join(general) or _("The bucket was not made. Nothing was saved."),
        }
        return _redirect_back(request)
    messages.success(request, _("Bucket %(name)s made.") % {"name": bucket.name})
    return redirect("vault:manage")


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@superuser_plan_door
@require_POST
def manage_edit(request, pk):
    bucket = get_object_or_404(Bucket.objects.select_related("owner"), pk=pk)
    data = request.POST
    posted = set(data.keys()) - FORM_NOISE - {"page"}
    changes = {}
    fields = set(bucket_lifecycle.editable())
    # Anything but these is refused, whole — by the rule's own sentence (the
    # AI shield among them where no assistant is installed).
    for extra in sorted(posted - fields)[:8]:
        changes[str(extra)[:40]] = None
    if "name" in posted:
        changes["name"] = " ".join(str(data.get("name", "") or "").split())
    if "owner" in posted:
        changes["owner"] = _owner_from(data.get("owner"))
    if "storage_quota_mb" in posted:
        changes["storage_quota_mb"] = data.get("storage_quota_mb")
    if "ai_protected" in posted and "ai_protected" in fields:
        changes["ai_protected"] = _checked(data, "ai_protected")
    try:
        changed = bucket_lifecycle.update_bucket(bucket, request.user, **changes)
    except ValidationError as exc:
        errors: dict = {}
        _merge(errors, exc)
        general = errors.pop("__all__", [])
        request.session[DRAFT_KEY] = {
            "open": "edit", "pk": bucket.pk,
            "name": str(changes.get("name", bucket.name) or "")[:NAME_MAX],
            "owner": person_row(changes["owner"]) if "owner" in changes else person_row(bucket.owner),
            "storage_quota_mb": str(data.get("storage_quota_mb", "") or "")[:12]
            if "storage_quota_mb" in posted else
            ("" if bucket.storage_quota_mb is None else str(bucket.storage_quota_mb)),
            **({"ai_protected": changes.get("ai_protected", bucket.ai_protected)}
               if "ai_protected" in fields else {}),
            "values": {}, "errors": errors,
            "error": " ".join(general) or _("Nothing was changed."),
        }
        return _redirect_back(request)
    if changed:
        messages.success(request, _("Bucket %(name)s saved.") % {"name": bucket.name})
    else:
        messages.info(request, _("Nothing to change in %(name)s.") % {"name": bucket.name})
    return _redirect_back(request)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@superuser_plan_door
@require_POST
def manage_delete(request, pk):
    bucket = get_object_or_404(Bucket, pk=pk)
    try:
        bucket_lifecycle.request_deletion(bucket, request.user,
                                          confirm_name=request.POST.get("confirm_name", ""))
    except ValidationError as exc:
        errors: dict = {}
        _merge(errors, exc)
        sentences = [s for group in errors.values() for s in group]
        request.session[DRAFT_KEY] = {
            "open": "delete", "pk": bucket.pk, "name": "", "values": {}, "errors": {},
            "error": " ".join(sentences) or _("Nothing was deleted."),
        }
        return _redirect_back(request)
    messages.success(request, _(
        "%(name)s is being deleted. It stays in the list, marked, until its "
        "files are gone.") % {"name": bucket.name})
    return _redirect_back(request)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@superuser_plan_door(json=True)
@require_POST
def manage_test(request, pk):
    """The connection test: one bounded call on a click, stamped on the bucket
    (a mount's on its pairing) so the list's health column learns from it."""
    bucket = Bucket.objects.select_related("peer", "provider").filter(pk=pk).first()
    adapter = StorageAdapter.for_bucket(bucket) if bucket is not None else None
    if adapter is None:
        response = JsonResponse({"ok": False, "error": _("No such bucket.")}, status=404)
    else:
        try:
            ok, detail = adapter.probe(bucket)
        except Exception as exc:  # noqa: BLE001 - one sentence, whatever broke
            ok, detail = False, str(exc)
        info = adapter.describe(bucket)
        response = JsonResponse({"ok": bool(ok), "detail": str(detail or ""),
                                 "health": info["health"], "health_label": str(info["health_label"])})
    response["Cache-Control"] = "no-store"
    return response
