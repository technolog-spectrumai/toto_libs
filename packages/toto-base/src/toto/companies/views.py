"""The company doors (stage 65): the ID number and the holdings.

Each door, in this order: POST only (405); somebody signed in (403); the
community the address names, which must be a company (404 otherwise, a
community that is none included); its head or an administrator (403:
``access.may_manage``, never staff alone); and, where the address names a
holding, a holding of THAT company (404 otherwise). Then the change, in one
transaction, and its audit record.

What was typed wrong is no refusal of the person: the door changes nothing,
says why in a message and goes back to the page, as it does after a change.
"""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.utils.translation import gettext as _

from . import access, audit
from .models import ID_NUMBER_MAX, CompanyRecord, ShareHolding
from .register import parse_quantity

#: The Shareholdings tab's name in the page's address.
TAB = "shareholdings"


def door(view):
    """A company door: see the module's docstring for the order."""

    @wraps(view)
    def wrapped(request, slug, *args, **kwargs):
        from toto.socialhub.models import Community

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not request.user.is_authenticated:
            raise PermissionDenied
        community = Community.objects.filter(slug=slug).first()
        if community is None or not access.is_company(community):
            raise Http404
        if not access.may_manage(request.user, community):
            raise PermissionDenied
        return view(request, community, *args, **kwargs)

    wrapped.companies_door = True
    return wrapped


def _back(community, tab=""):
    from toto.socialhub.views.community import community_page_url

    return HttpResponseRedirect(community_page_url(community, tab))


def _printable(text: str) -> bool:
    return all(character.isprintable() for character in text)


@door
def number(request, community):
    """Set, change or clear the company's ID number: text, kept as typed but
    for the spaces at its ends."""
    typed = (request.POST.get("id_number") or "").strip()
    if len(typed) > ID_NUMBER_MAX:
        messages.error(request, _("The ID number is too long: at most %(max)d characters.")
                       % {"max": ID_NUMBER_MAX})
        return _back(community)
    if not _printable(typed):
        messages.error(request, _("The ID number is one line of text."))
        return _back(community)
    with transaction.atomic():
        try:
            with transaction.atomic():
                record, _made = (CompanyRecord.objects.select_for_update()
                                 .get_or_create(community=community))
        except IntegrityError:
            # Somebody made the row between the look and the insert.
            record = CompanyRecord.objects.select_for_update().get(community=community)
        before = record.id_number
        if before != typed:
            record.id_number = typed
            record.updated_by = request.user
            record.save(update_fields=["id_number", "updated_by", "updated_at"])
            audit.number_changed(request.user, community, before=before, after=typed)
            messages.success(request, _("The company's ID number was saved."))
    return _back(community)


@door
def holding_save(request, community):
    """Record a person's holding, or change its quantity: one row per
    company and person, whatever two managers do at once."""
    from toto.people.models import Person

    person = Person.objects.filter(slug=(request.POST.get("person") or "").strip()).first()
    if person is None:
        messages.error(request, _("Choose the person who holds the shares."))
        return _back(community, TAB)
    quantity = parse_quantity(request.POST.get("quantity"))
    if quantity is None:
        messages.error(request, _("The quantity is a whole number of shares, zero or more."))
        return _back(community, TAB)
    with transaction.atomic():
        holding = (ShareHolding.objects.select_for_update()
                   .filter(community=community, person=person).first())
        created = False
        if holding is None:
            try:
                with transaction.atomic():
                    holding = ShareHolding.objects.create(
                        community=community, person=person, quantity=quantity)
                created = True
            except IntegrityError:
                # The other save made it first: this one changes that row.
                holding = (ShareHolding.objects.select_for_update()
                           .get(community=community, person=person))
        if created:
            audit.holding(audit.HOLDING_RECORDED, request.user, community, person,
                          holding_id=holding.pk, after=quantity)
            messages.success(request, _("The holding was recorded."))
        elif holding.quantity != quantity:
            before = holding.quantity
            holding.quantity = quantity
            holding.save(update_fields=["quantity", "updated_at"])
            audit.holding(audit.HOLDING_CHANGED, request.user, community, person,
                          holding_id=holding.pk, before=before, after=quantity)
            messages.success(request, _("The holding was changed."))
    return _back(community, TAB)


@door
def holding_delete(request, community, pk):
    """Take a holding out of the register. Only a holding of the company in
    the address: another company's answers 404, as one that is not there."""
    with transaction.atomic():
        holding = (ShareHolding.objects.select_for_update()
                   .filter(pk=pk, community=community).first())
        if holding is None:
            raise Http404
        person, before, holding_id = holding.person, holding.quantity, holding.pk
        holding.delete()
        audit.holding(audit.HOLDING_REMOVED, request.user, community, person,
                      holding_id=holding_id, before=before)
        messages.success(request, _("The holding was removed."))
    return _back(community, TAB)
