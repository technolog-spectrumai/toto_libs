"""Who reads a bucket's files: the bucket's clearances (2026-09-30).

Clearances go on groups, never on items — in the vault the group is the
BUCKET (``BucketClearance``; a file has one bucket, and a file in no bucket is
never kept). The rule is ``socialhub.clearance_access`` and it is enforced in
``access.may_read`` / ``access.gate_by_bucket``: a file in a bucket kept to
clearances is read by superusers and by holders of one of them — not by its
owner, the public flag, the bucket's owner or a folder's ACL; a file in a bucket
with none follows the vault's five clauses.

This module is the door for CHANGING a bucket's clearances — a POST from the
"Clearances" section of the bucket's page (``metrics/<slug>/``), and the
helpers that page asks. Only superusers set them; whoever may see the bucket's
page sees them, read-only.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.socialhub import clearance_access

from .models import Bucket

ROWS = "clearance_rows"


def may_manage(user) -> bool:
    """Only superusers set a bucket's clearances."""
    return bool(getattr(user, "is_authenticated", False) and user.is_superuser)


def clearances_of(bucket) -> list:
    return clearance_access.clearances_of(bucket, rows=ROWS)


def set_clearances(bucket, clearances, *, actor):
    return clearance_access.set_clearances(bucket, clearances, rows=ROWS, actor=actor,
                                           action="bucket.clearances_changed",
                                           app_label="vault", bucket=bucket.pk,
                                           slug=bucket.slug, name=bucket.name)


def page_context(user, bucket) -> dict:
    """What the bucket page's "Clearances" section needs: the bucket's
    clearances for every viewer, the checkboxes for a superuser."""
    current = clearances_of(bucket)
    context = {"bucket_clearances": current, "bucket_clearance_choices": [],
               "bucket_clearances_url": ""}
    if may_manage(user):
        from toto.socialhub.models import Clearance

        on = {c.pk for c in current}
        context["bucket_clearance_choices"] = [
            {"clearance": c, "on": c.pk in on} for c in Clearance.objects.order_by("name")]
        context["bucket_clearances_url"] = reverse("vault:bucket_clearances", args=[bucket.slug])
    return context


def _ids(values) -> set:
    return {int(v) for v in values if str(v).isascii() and str(v).isdigit() and len(str(v)) <= 18}


@login_required
@require_POST
def bucket_clearances(request, bucket_slug):
    """Save the bucket's clearances (superusers only), back to its page. The
    bucket's owner is refused; anybody else gets the 404 the page gives."""
    bucket = get_object_or_404(Bucket, slug=bucket_slug)
    if not may_manage(request.user):
        if bucket.owner_id != request.user.pk:
            raise Http404("No such bucket.")
        return HttpResponseForbidden(_("Only a superuser sets a bucket's clearances."))
    from toto.socialhub.models import Clearance

    picked = Clearance.objects.filter(pk__in=_ids(request.POST.getlist("clearance")))
    before, after = set_clearances(bucket, picked, actor=request.user)
    messages.success(request, _("Saved.") if before != after else _("Nothing changed."))
    return redirect(reverse("vault:bucket_metrics", args=[bucket.slug]) + "#clearances")
