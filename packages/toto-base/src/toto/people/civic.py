"""
Civic status helpers — computed from ConstitutionSignature, no model field required.

is_committed_citizen(person) — single-person check.
committed_citizen_filter()   — Q() for use in ORM .filter() on Person querysets.
"""
from __future__ import annotations

from django.db.models import Q


def is_committed_citizen(person) -> bool:
    """
    Return True if person has signed at least one community's active constitution.

    Proven digital citizenship / trusted civic participation.
    Grants: responder eligibility, assembly broad access, poll-tax exemption,
    civic voting rights, eligibility for non-enforcement public roles.
    """
    from toto.socialhub.models import ConstitutionSignature
    return ConstitutionSignature.objects.filter(
        person=person,
        signed_at__isnull=False,
    ).exists()


def committed_citizen_filter() -> Q:
    """
    Return a Q() that matches Person PKs where is_committed_citizen is True.

    Usage:
        Person.objects.filter(committed_citizen_filter() | Q(communities__in=tribe_ids))
    """
    from toto.socialhub.models import ConstitutionSignature
    signed_person_ids = ConstitutionSignature.objects.filter(
        signed_at__isnull=False,
    ).values("person_id")
    return Q(pk__in=signed_person_ids)


def get_signed_constitutions(person):
    """Return ConstitutionSignature qs for person (signed only), with related constitution/community."""
    from toto.socialhub.models import ConstitutionSignature
    return (
        ConstitutionSignature.objects
        .filter(person=person, signed_at__isnull=False)
        .select_related("constitution", "constitution__community")
        .order_by("signed_at")
    )
