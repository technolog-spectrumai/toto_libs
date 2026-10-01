"""Storage → Management: a bucket shared with another Zenobia, both sides
(2026-09-30).

Two Zenobias link one bucket with a **pairing code** — base64 over JSON
(``peering.pairing_code_for``) carrying the grant's id, its magic token and a
freshly minted api key. The code is made on the Zenobia that HOLDS the files
and entered on the one that wants to USE them. It is a live credential:
whoever holds it (or a photo of its QR code) can do what the share allows
until the share ends or is revoked.

**Sharing side** — a bucket's "Share with another Zenobia" (a local or S3
bucket; never one connected from a third Zenobia, ``BucketGrant.clean``):

* ``manage_shares`` (GET, JSON) — the bucket's "Shared with" list: label,
  rights, until when, made when and by whom, last use, status. Never the
  grant's id, token, key or key hint.
* ``manage_share`` (POST) — who it is for, the rights (all OFF until ticked;
  at least one, and List whenever any is: connecting reads the share's
  manifest, which needs it — the form ticks it with any other right), how
  long (7 days by default), this Zenobia's address as the
  other one reaches it. Makes the ``BucketGrant`` (hash only) and answers
  with the ONE page fragment that ever carries the code: the code, a copy
  button, the place its QR code is drawn IN THE BROWSER from exactly that
  string (``vendor/qrcodejs``, never a QR service), numbered instructions for
  the other administrator, "will not be shown again". ``no-store``. The code
  is not kept anywhere: not in a column, the session, a message, JSON, a log
  or the audit chain.
* ``manage_share_rotate`` (POST) — a new key for a share (and, optionally, a
  new end date); the old key stops working at once; the new code is shown
  the same way, once.
* ``manage_share_revoke`` (POST, JSON) — the share stops working at once. The
  row stays, revoked, for the record (``is_active = False``, the admin's
  rule); a revoked share is never revived — make a new one.

**Connecting side** — "Connect a bucket from another Zenobia", a stepper whose
every door is JSON and takes the code in the POST body, from the browser's
memory; the server never sends it back:

1. the code — pasted, scanned with the camera, or read from an image of its
   QR code, all in the browser (the image is never uploaded);
2. ``manage_connect_preview`` — what the code grants (the other Zenobia's
   address, its bucket, the rights, until when) before anything is saved;
   a malformed, expired or already-connected code is refused with what to do
   (an already-connected one whose key was rotated there offers
   ``manage_connect_renew``: the stored key replaced, after the new one
   answered);
3. ``manage_connect_test`` — the connection test (the peer's manifest, over
   the SSRF guard) with its answer; nothing saved;
4. ``manage_connect`` — a local name and an owner; the test runs again and
   must pass, then the pairing and the bucket are made in one transaction
   (``ZenobiaRemoteAdapter.create``).

Every door: a superuser ON THE SUPERUSER PLAN (``plan_gate``), JSON 403 for
anyone else before anything is looked up. Every error sentence is scrubbed of
the code and its parts; ``sensitive_post_parameters`` keeps the code out of
error reports.

Audit (``bucket_lifecycle.audit``, never a secret in the metadata):
``VAULT.BUCKET.SHARED``, ``VAULT.BUCKET.SHARE_ROTATED``,
``VAULT.BUCKET.SHARE_REVOKED``, ``VAULT.BUCKET.PEER_KEY_REPLACED``; a
connected bucket is ``VAULT.BUCKET.CREATED`` (the adapter's).
"""

from __future__ import annotations

import base64
import datetime
import hmac
import json
import uuid

from django.apps import apps
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST, require_safe

from . import bucket_lifecycle
from .manage_views import _checked, _display_name, _owner_from, _scrub, person_row
from .models import Bucket, StorageBackend, closed_bucket_sentence, external_buckets_allowed
from .peering import BUCKET_RIGHTS, BucketGrant, BucketPeer, federated_host_choices, pairing_code_for
from .plan_gate import superuser_plan_door
from .storage_adapters import NAME_MAX, StorageAdapter, clean_name, clean_owner, require_field_key

#: The adapter the connecting side goes through.
REMOTE_KIND = "zenobia_remote"
#: ``BucketGrant.label``'s own limit.
LABEL_MAX = 180
#: A pairing code is a few hundred characters; anything past this is not one.
CODE_MAX = 4000
#: How long a share lasts, as the form offers it. Seven days is the default
#: (``peering._default_expiry``): a share is a live credential, extended
#: deliberately (Rotate key, with a new end date) once the link works.
EXPIRY_DAYS = {"1": 1, "7": 7, "30": 30, "90": 90, "365": 365}
DEFAULT_EXPIRY = "7"
NEVER = "never"
KEEP = "keep"
#: What a refused connect door says went wrong, as the stepper's step.
STEP_OF_FIELD = {"pairing_code": "code", "base_url": "address", "test": "test",
                 "name": "name", "owner": "name"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _refuse(error, *, errors=None, status=400, **extra):
    return _json({"ok": False, "error": str(error), "errors": errors or {}, **extra}, status=status)


def _when(value) -> str:
    if not value:
        return ""
    return formats.date_format(timezone.localtime(value), "SHORT_DATETIME_FORMAT")


def _sentences(exc: ValidationError, secrets=()) -> dict:
    """``{field: [sentences]}`` (``__all__`` for the ones naming no field),
    every sentence scrubbed of what the code carried. A sentence raised in
    English is looked up here; one already translated passes through
    unchanged (``peering``'s decode sentences are translated where they are
    raised since 2026-10-01)."""
    if hasattr(exc, "error_dict"):
        items = exc.message_dict.items()
    else:
        items = [("__all__", exc.messages)]
    return {name: [_scrub(_(str(s)), secrets) for s in sentences] for name, sentences in items}


def _first(errors: dict) -> str:
    for sentences in errors.values():
        if sentences:
            return sentences[0]
    return ""


def _step(errors: dict) -> str:
    for name in errors:
        if name in STEP_OF_FIELD:
            return STEP_OF_FIELD[name]
    return ""


def _uuid_ok(value) -> bool:
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return False
    return True


@sensitive_variables("raw", "payload")
def _code_secrets(raw) -> list:
    """The code and everything secret inside it, for scrubbing sentences."""
    raw = str(raw or "").strip()
    values = [raw]
    try:
        payload = json.loads(base64.b64decode(raw.encode()))
    except Exception:  # noqa: BLE001 - a malformed code is scrubbed whole
        payload = None
    if isinstance(payload, dict):
        values += [str(payload.get(k) or "") for k in ("api_key", "magic_token", "grant_uid")]
    return [v for v in values if len(v) >= 6]


def own_address(request) -> str:
    """This Zenobia as the request reached it — what a code names as its host
    unless the form says otherwise."""
    if request is None:
        return ""
    try:
        return request.build_absolute_uri("/").rstrip("/")
    except Exception:  # noqa: BLE001 - a host header Django refuses: nothing to offer
        return ""


def _clean_address(request, raw) -> str:
    """This Zenobia's address for the code: typed, or the request's own. Only
    its shape is checked here — the OTHER side guards what it calls."""
    from .outbound import OutboundRefused, canonical_outbound_url

    raw = str(raw or "").strip()
    if not raw:
        return own_address(request)
    if len(raw) > ADDRESS_MAX:
        raise ValidationError(_("An address has at most 200 characters."))
    try:
        return canonical_outbound_url(raw, label=_("This Zenobia's address")).rstrip("/")
    except OutboundRefused as exc:
        raise ValidationError(str(exc))


def _expiry(raw, *, grant=None):
    """The end date a posted choice means: a number of days from now, no end
    date, or — when rotating a share that has not ended — its own."""
    raw = str(raw or "").strip() or DEFAULT_EXPIRY
    if raw == KEEP and grant is not None and not grant.is_expired:
        return grant.expires_at
    if raw == NEVER:
        return None
    if raw in EXPIRY_DAYS:
        return timezone.now() + datetime.timedelta(days=EXPIRY_DAYS[raw])
    raise ValidationError(_("Choose how long the share lasts."))


def expiry_choices() -> list:
    choices = []
    for value, days in EXPIRY_DAYS.items():
        label = (ngettext("%(n)s day", "%(n)s days", days) % {"n": days}) if days < 365 else _("1 year")
        choices.append({"value": value, "label": label})
    choices.append({"value": NEVER, "label": _("No end date — until revoked")})
    return choices


def _right_labels(rights) -> list:
    from .plugins.storage_adapters import RIGHT_LABELS

    return [str(RIGHT_LABELS[r]) for r in rights if r in RIGHT_LABELS]


# ---------------------------------------------------------------------------
# What the page needs (templatetags/vault_share.py hands it to the templates)
# ---------------------------------------------------------------------------

def _remote_adapter():
    """The adapter the connecting side uses — None on a host that allows no
    external bucket (``VAULT_EXTERNAL_BUCKETS = False``)."""
    return StorageAdapter.for_key(REMOTE_KIND)


def connect_refusal() -> str:
    """Why this server cannot connect another Zenobia's bucket, or ""."""
    if _remote_adapter() is None:
        return _("External buckets are disabled on this server.")
    try:
        require_field_key()
    except ValidationError as exc:
        return " ".join(exc.messages)
    return ""


def share_refusal(bucket) -> str:
    """Why this bucket cannot be shared, or ""."""
    if not external_buckets_allowed():
        return _("Sharing with another Zenobia is disabled on this server.")
    if bucket.storage_backend == StorageBackend.REMOTE_TOTO:
        return _("A bucket connected from another Zenobia cannot be shared onwards: share it "
                 "from the Zenobia that holds its files.")
    if bucket.is_being_deleted:
        return closed_bucket_sentence(bucket)
    return ""


def page_config(request) -> dict:
    """The share and connect modals' settings, for ``json_script``. Nothing
    secret: addresses, names, links and sentences."""
    adapter = _remote_adapter()
    remote_ok = external_buckets_allowed()
    hosts = [{"value": v, "label": str(l)[:LABEL_MAX]} for v, l in federated_host_choices()] \
        if remote_ok else []
    user = getattr(request, "user", None)
    return {
        "share_enabled": remote_ok,
        "connect_enabled": adapter is not None,
        "connect_blocked": connect_refusal() if adapter is not None else "",
        "hosts": hosts,
        "address": own_address(request),
        "me": person_row(user) if getattr(user, "is_authenticated", False) else None,
        "name_max": NAME_MAX,
        "label_max": LABEL_MAX,
        "expiry_choices": expiry_choices(),
        "default_expiry": DEFAULT_EXPIRY,
        "urls": {
            "preview": reverse("vault:manage_connect_preview"),
            "test": reverse("vault:manage_connect_test"),
            "connect": reverse("vault:manage_connect"),
            "renew": reverse("vault:manage_connect_renew"),
        },
        "texts": {
            "failed": _("The server could not be reached. Try again."),
            "paste_first": _("Paste the code, scan it or choose an image of it first."),
            "address_first": _("Enter the other Zenobia's address."),
            "scanning": _("Hold the QR code in front of the camera."),
            "no_qr_yet": _("No QR code found yet — hold it steady and closer, so it fills most of the picture."),
            "code_read": _("Code read."),
            "camera_refused": _("The camera was refused. Allow it in the browser's site settings, "
                                "or paste the code or choose an image instead."),
            "no_camera": _("No camera was found. Paste the code or choose an image instead."),
            "camera_unavailable": _("This browser cannot use a camera on this page (it needs https). "
                                    "Paste the code or choose an image instead."),
            "camera_failed": _("The camera could not be started. Paste the code or choose an image instead."),
            "no_decoder": _("This browser cannot read QR codes here. Paste the code instead."),
            "not_image": _("That file is not an image this browser can open."),
            "no_qr_in_image": _("No QR code was found in that image. Try a sharper picture, or paste the code."),
            "no_qr_drawn": _("The QR code could not be drawn in this browser; the text code works the same."),
        },
    }


# ---------------------------------------------------------------------------
# Sharing: the list
# ---------------------------------------------------------------------------

def share_status(grant) -> str:
    if not grant.is_active:
        return "revoked"
    if grant.is_expired:
        return "expired"
    return "active"


def _status_label(status) -> str:
    return {"active": _("Active"), "expired": _("Ended"), "revoked": _("Revoked")}.get(status, status)


def share_row(grant) -> dict:
    """A share as the "Shared with" list shows it — never its id, token, key
    or key hint."""
    status = share_status(grant)
    rights = [r for r in BUCKET_RIGHTS if getattr(grant, r)]
    return {
        "id": grant.pk,
        "label": grant.label,
        "rights": rights,
        "rights_labels": _right_labels(rights),
        "status": status,
        "status_label": _status_label(status),
        "expires": _when(grant.expires_at),
        "expires_in_future": bool(grant.expires_at and not grant.is_expired),
        "created": _when(grant.created_at),
        "created_by": _display_name(grant.created_by) if grant.created_by_id else "",
        "last_used": _when(grant.last_read_at),
        "uses": grant.read_count,
        "rotated": _when(grant.key_rotated_at),
        "rotate_url": reverse("vault:manage_share_rotate", args=[grant.pk]),
        "revoke_url": reverse("vault:manage_share_revoke", args=[grant.pk]),
    }


def shares_of(bucket) -> list:
    grants = BucketGrant.objects.filter(bucket=bucket).select_related("created_by")
    if apps.is_installed("toto.people"):
        grants = grants.select_related("created_by__community_profile")
    return [share_row(g) for g in grants.order_by("-created_at", "-pk")]


def _listing(bucket) -> dict:
    return {
        "ok": True,
        "bucket": {"pk": bucket.pk, "name": bucket.name},
        "refusal": share_refusal(bucket),
        "shares": shares_of(bucket),
        "share_url": reverse("vault:manage_share", args=[bucket.pk]),
    }


@superuser_plan_door(json=True)
@require_safe
def manage_shares(request, pk):
    bucket = Bucket.objects.filter(pk=pk).first()
    if bucket is None:
        return _refuse(_("No such bucket."), status=404)
    return _json(_listing(bucket))


# ---------------------------------------------------------------------------
# Sharing: make, rotate, revoke
# ---------------------------------------------------------------------------

def _audit_share(action, grant, actor, **extra):
    return bucket_lifecycle.audit(
        action, grant.bucket, actor, share=grant.pk, label=grant.label,
        rights=[r for r in BUCKET_RIGHTS if getattr(grant, r)],
        expires_at=grant.expires_at.isoformat() if grant.expires_at else None, **extra)


@sensitive_variables("code")
def _code_response(request, grant, code, *, rotated):
    """The one answer that carries the code: a fragment of the share modal,
    never stored, never cached."""
    rights = [r for r in BUCKET_RIGHTS if getattr(grant, r)]
    response = render(request, "vault/manage/_share_code.html", {
        "grant": grant,
        "bucket": grant.bucket,
        "code": code,
        "rights_labels": _right_labels(rights),
        "may_delete": grant.may_delete,
        "expires": _when(grant.expires_at),
        "rotated": rotated,
    })
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


@superuser_plan_door(json=True)
@sensitive_variables("raw_key", "code")
@require_POST
def manage_share(request, pk):
    bucket = Bucket.objects.filter(pk=pk).first()
    if bucket is None:
        return _refuse(_("No such bucket."), status=404)
    refusal = share_refusal(bucket)
    if refusal:
        return _refuse(refusal, status=409)
    data = request.POST
    errors: dict = {}
    label = " ".join(str(data.get("label", "") or "").split())
    if not label:
        errors["label"] = [_("Say who the share is for.")]
    elif len(label) > LABEL_MAX:
        errors["label"] = [_("At most 180 characters.")]
    rights = {right: _checked(data, right) for right in BUCKET_RIGHTS}
    if not any(rights.values()):
        errors["rights"] = [_("Tick at least one right: a share without rights lets the other "
                              "Zenobia do nothing.")]
    elif not rights["may_list"]:
        # The other Zenobia connects by reading the share's manifest, which
        # needs List (peer_views.peer_manifest) — as does every refresh of
        # its listing. Without it the code could never be connected.
        errors["rights"] = [_("Tick List too: the other Zenobia needs it to connect the bucket "
                              "and see its files, whatever else it may do.")]
    try:
        expires_at = _expiry(data.get("expires"))
    except ValidationError as exc:
        errors["expires"] = exc.messages
    try:
        host = _clean_address(request, data.get("address"))
    except ValidationError as exc:
        errors["address"] = exc.messages
    if errors:
        return _refuse(_("Nothing was shared."), errors=errors)
    grant = BucketGrant(label=label, bucket=bucket, created_by=request.user,
                        expires_at=expires_at, **rights)
    try:
        grant.clean()
    except ValidationError:
        return _refuse(share_refusal(bucket) or _("Nothing was shared."), status=409)
    with transaction.atomic():
        raw_key = grant.issue_api_key()
        grant.save()
        _audit_share("shared", grant, request.user)
    code = pairing_code_for(grant, raw_key, host=host)
    return _code_response(request, grant, code, rotated=False)


@superuser_plan_door(json=True)
@sensitive_variables("raw_key", "code")
@require_POST
def manage_share_rotate(request, grant_pk):
    grant = BucketGrant.objects.select_related("bucket").filter(pk=grant_pk).first()
    if grant is None:
        return _refuse(_("No such share."), status=404)
    if not grant.is_active:
        return _refuse(_("This share was revoked, and a revoked share stays revoked: make a new "
                         "share instead."), status=409)
    refusal = share_refusal(grant.bucket)
    if refusal:
        return _refuse(refusal, status=409)
    errors: dict = {}
    try:
        expires_at = _expiry(request.POST.get("expires"), grant=grant)
    except ValidationError as exc:
        errors["expires"] = exc.messages
    try:
        host = _clean_address(request, request.POST.get("address"))
    except ValidationError as exc:
        errors["address"] = exc.messages
    if errors:
        return _refuse(_("The key was not changed."), errors=errors)
    with transaction.atomic():
        grant = BucketGrant.objects.select_for_update().select_related("bucket").get(pk=grant.pk)
        if not grant.is_active:  # revoked meanwhile
            return _refuse(_("This share was revoked, and a revoked share stays revoked: make a "
                             "new share instead."), status=409)
        raw_key = grant.issue_api_key()
        grant.key_rotated_at = timezone.now()
        grant.expires_at = expires_at
        grant.save(update_fields=["api_key_hash", "api_key_hint", "key_rotated_at", "expires_at"])
        _audit_share("share_rotated", grant, request.user)
    code = pairing_code_for(grant, raw_key, host=host)
    return _code_response(request, grant, code, rotated=True)


@superuser_plan_door(json=True)
@require_POST
def manage_share_revoke(request, grant_pk):
    grant = BucketGrant.objects.select_related("bucket").filter(pk=grant_pk).first()
    if grant is None:
        return _refuse(_("No such share."), status=404)
    revoked = BucketGrant.objects.filter(pk=grant.pk, is_active=True).update(is_active=False)
    if revoked:
        grant.is_active = False
        _audit_share("share_revoked", grant, request.user)
    listing = _listing(grant.bucket)
    listing["message"] = _("The share for %(label)s is revoked: the other Zenobia can no longer "
                           "use this bucket.") % {"label": grant.label}
    return _json(listing)


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------

def _connect_door_refusal():
    """A JSON refusal when this server cannot connect at all, else None."""
    refusal = connect_refusal()
    if refusal:
        return _refuse(refusal, status=409)
    return None


#: The columns a code's values land in (``BucketPeer``): a longer value is
#: not a code this suite minted.
TOKEN_MAX = 128
KEY_MAX = 512
SLUG_MAX = 140
#: ``BucketPeer.base_url`` is a URLField (200).
ADDRESS_MAX = 200


def _shape_ok(payload) -> bool:
    """Every value where the wire format puts it, of the type it has there —
    a hand-made code must be refused with a sentence, never crash a door or
    overflow a column."""
    for key, limit in (("grant_uid", 64), ("magic_token", TOKEN_MAX), ("api_key", KEY_MAX)):
        value = payload.get(key)
        if not isinstance(value, str) or not value or len(value) > limit:
            return False
    if not _uuid_ok(payload["grant_uid"]):
        return False
    rights = payload.get("rights", [])
    if rights is not None and not (isinstance(rights, list) and all(isinstance(r, str) for r in rights)):
        return False
    for key, limit in (("bucket", SLUG_MAX), ("host", 2 * ADDRESS_MAX), ("expires_at", 64)):
        value = payload.get(key)
        if value is not None and not (isinstance(value, str) and len(value) <= limit):
            return False
    return True


def _malformed():
    return ValidationError({"pairing_code": _(
        "That does not decode as a pairing code. Paste the whole code, with no "
        "surrounding quotes or line breaks.")})


@sensitive_variables("raw", "payload")
def _read_code(adapter, raw):
    """``(payload, preview)`` of a pasted code, or ValidationError on
    ``pairing_code`` with the sentence that says what to do."""
    from .peering import decode_pairing_code

    raw = str(raw or "")
    if len(raw) > CODE_MAX:
        raise _malformed()
    if not raw.strip():
        raise ValidationError({"pairing_code": _("Paste the pairing code.")})
    try:
        payload = decode_pairing_code(raw)      # "does not decode" / "unsupported version"
    except ValidationError as exc:
        raise ValidationError({"pairing_code": exc.messages})
    if not _shape_ok(payload):
        raise _malformed()
    return payload, adapter.decode_preview(raw)


def _clean_base_url(data) -> None:
    """The typed address, bounded by its column (the SSRF guard is the
    adapter's)."""
    if len(str(data.get("base_url", "") or "").strip()) > ADDRESS_MAX:
        raise ValidationError({"base_url": _("An address has at most 200 characters.")})


@sensitive_variables("payload")
def _connected_peer(payload):
    """The pairing this code already made here (same grant, same token), or None."""
    peer = BucketPeer.objects.filter(grant_uid=str(payload.get("grant_uid"))).first()
    if peer is None:
        return None
    if not hmac.compare_digest(str(peer.magic_token), str(payload.get("magic_token") or "")):
        return None
    return peer


@sensitive_variables("payload", "stored")
def _same_key(peer, payload) -> bool:
    try:
        stored = peer.get_api_key()
    except Exception:  # noqa: BLE001 - a key sealed under another FIELD_ENCRYPTION_KEY
        return False
    return bool(stored) and hmac.compare_digest(stored, str(payload.get("api_key") or ""))


@superuser_plan_door(json=True)
@sensitive_post_parameters("pairing_code")
@sensitive_variables("raw", "payload")
@require_POST
def manage_connect_preview(request):
    """Step 2: what a code grants, before anything is saved. The answer names
    the other Zenobia, its bucket, the rights and the end date — nothing that
    was secret in the code."""
    refused = _connect_door_refusal()
    if refused is not None:
        return refused
    adapter = _remote_adapter()
    raw = str(request.POST.get("pairing_code", "") or "")
    secrets = _code_secrets(raw)
    try:
        payload, preview = _read_code(adapter, raw)
    except ValidationError as exc:
        errors = _sentences(exc, secrets)
        return _refuse(_first(errors), errors=errors, step="code")
    if preview["expired"]:
        sentence = _("This pairing code has expired. Ask the other Zenobia's operator for a new one.")
        return _refuse(sentence, errors={"pairing_code": [sentence]}, step="code")
    if preview["lacks_list"] and not preview["already_connected"]:
        from .plugins.storage_adapters import lacks_list_sentence

        sentence = lacks_list_sentence()
        return _refuse(sentence, errors={"pairing_code": [sentence]}, step="code")
    if preview["already_connected"]:
        peer = _connected_peer(payload)
        same = peer is not None and _same_key(peer, payload)
        sentence = _("This pairing code is already connected here, as '%(label)s'.") % {
            "label": preview["already_connected"]}
        if peer is None:
            advice = _("Nothing more can be done with it here: ask the other Zenobia's operator "
                       "for a new share.")
        elif same:
            advice = _("It already uses this very key: there is nothing to do.")
        else:
            advice = _("If the other Zenobia rotated its key, replace the key stored here with "
                       "this one to keep the connection working.")
        return _refuse(f"{sentence} {advice}", errors={"pairing_code": [sentence]}, step="code",
                       connected={"label": preview["already_connected"],
                                  "renewable": bool(peer is not None and not same)})
    expires_at = preview["expires_at"]
    name = str(preview["bucket"] or "")[:NAME_MAX]
    return _json({
        "ok": True,
        "preview": {
            "host": preview["host"],
            "bucket": preview["bucket"],
            "rights": preview["rights"],
            "rights_labels": preview["rights_labels"],
            "may_delete": "may_delete" in preview["rights"],
            "expires": _when(expires_at),
        },
        "name": name,
    })


@superuser_plan_door(json=True)
@sensitive_post_parameters("pairing_code")
@sensitive_variables("config", "secret")
@require_POST
def manage_connect_test(request):
    """Step 3: the connection test — the other Zenobia's manifest, over the
    SSRF guard, with the code's key. Nothing is saved."""
    refused = _connect_door_refusal()
    if refused is not None:
        return refused
    adapter = _remote_adapter()
    raw = str(request.POST.get("pairing_code", "") or "")
    secrets = _code_secrets(raw)
    try:
        _read_code(adapter, raw)
        _clean_base_url(request.POST)
        config, secret = adapter.validate(request.POST)
    except ValidationError as exc:
        errors = _sentences(exc, secrets)
        return _refuse(_first(errors), errors=errors, step=_step(errors))
    try:
        ok, detail = adapter.probe_candidate(config, secret)
    except Exception as exc:  # noqa: BLE001 - one sentence, whatever broke
        ok, detail = False, str(exc)
    return _json({"ok": bool(ok), "detail": _scrub(str(detail or ""), secrets + list(secret.values())),
                  "base_url": config["base_url"]})


@superuser_plan_door(json=True)
@sensitive_post_parameters("pairing_code")
@sensitive_variables("config", "secret")
@require_POST
def manage_connect(request):
    """Step 4: a name and an owner, then Connect — the test again (it must
    pass), then the pairing and the bucket in one transaction."""
    refused = _connect_door_refusal()
    if refused is not None:
        return refused
    adapter = _remote_adapter()
    data = request.POST
    raw = str(data.get("pairing_code", "") or "")
    secrets = _code_secrets(raw)
    name = " ".join(str(data.get("name", "") or "").split())
    owner = _owner_from(data.get("owner"))
    errors: dict = {}
    for check in (lambda: clean_name(name), lambda: clean_owner(owner)):
        try:
            check()
        except ValidationError as exc:
            for field, sentences in _sentences(exc, secrets).items():
                errors.setdefault(field, []).extend(sentences)
    config = secret = None
    try:
        _read_code(adapter, raw)
        _clean_base_url(data)
        config, secret = adapter.validate(data)
    except ValidationError as exc:
        for field, sentences in _sentences(exc, secrets).items():
            errors.setdefault(field, []).extend(sentences)
    if not errors:
        try:
            ok, detail = adapter.probe_candidate(config, secret)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc)
        if not ok:
            reason = _scrub(str(detail or ""), secrets + list(secret.values()))
            errors["test"] = [_("The other Zenobia did not answer the connection test, so nothing "
                                "was connected: %(reason)s") % {"reason": reason}]
    if not errors:
        try:
            bucket = adapter.create(name, owner, request.user, config, secret,
                                    ai_protected=_checked(data, "ai_protected"))
        except ValidationError as exc:
            for field, sentences in _sentences(exc, secrets + list(secret.values())).items():
                errors.setdefault(field, []).extend(sentences)
        except IntegrityError:
            errors["__all__"] = [_("The name or the pairing was taken meanwhile. Nothing was saved.")]
    if errors:
        general = errors.pop("__all__", [])
        return _refuse(" ".join(general) or _first(errors) or _("Nothing was connected."),
                       errors=errors, step=_step(errors))
    messages.success(request, _("Bucket %(name)s is connected to the other Zenobia.") % {
        "name": bucket.name})
    return _json({"ok": True, "redirect": reverse("vault:manage")})


@superuser_plan_door(json=True)
@sensitive_post_parameters("pairing_code")
@sensitive_variables("raw", "payload", "secret")
@require_POST
def manage_connect_renew(request):
    """An already-connected code whose key the other Zenobia rotated: the new
    key is tried against the stored address first, and only then replaces the
    sealed one. The pairing, its buckets and their files stay as they are."""
    refused = _connect_door_refusal()
    if refused is not None:
        return refused
    adapter = _remote_adapter()
    raw = str(request.POST.get("pairing_code", "") or "")
    secrets = _code_secrets(raw)
    try:
        payload, preview = _read_code(adapter, raw)
    except ValidationError as exc:
        errors = _sentences(exc, secrets)
        return _refuse(_first(errors), errors=errors, step="code")
    if preview["expired"]:
        return _refuse(_("This pairing code has expired. Ask the other Zenobia's operator for a "
                         "new one."), step="code")
    peer = _connected_peer(payload)
    if peer is None:
        return _refuse(_("No connection here uses this pairing code: connect it as a new bucket "
                         "instead."), step="code")
    if _same_key(peer, payload):
        return _json({"ok": True, "unchanged": True,
                      "message": _("It already uses this very key: there is nothing to do.")})
    secret = {"grant_uid": str(peer.grant_uid), "magic_token": peer.magic_token,
              "api_key": str(payload.get("api_key") or "")}
    config = {"base_url": peer.base_url, "remote_bucket": peer.remote_bucket_slug,
              "rights": list(peer.capabilities or [])}
    try:
        ok, detail = adapter.probe_candidate(config, secret)
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc)
    if not ok:
        reason = _scrub(str(detail or ""), secrets + list(secret.values()))
        return _refuse(_("The other Zenobia did not accept the new key, so the stored one was "
                         "kept: %(reason)s") % {"reason": reason}, step="code")
    with transaction.atomic():
        peer = BucketPeer.objects.select_for_update().get(pk=peer.pk)
        peer.set_api_key(secret["api_key"])
        peer.probe_error = ""
        peer.last_error = ""
        peer.save(update_fields=["api_key_encrypted", "api_key_hint", "probe_error", "last_error"])
        buckets = list(Bucket.objects.filter(peer=peer).select_related("peer"))
        for bucket in buckets:
            bucket_lifecycle.audit("peer_key_replaced", bucket, request.user,
                                   connection=peer.label, target=peer.base_url)
    if buckets:
        adapter.probe(buckets[0])   # stamps the manifest (rights may have changed there)
    messages.success(request, _("The key stored for %(label)s was replaced.") % {"label": peer.label})
    return _json({"ok": True, "redirect": reverse("vault:manage")})
