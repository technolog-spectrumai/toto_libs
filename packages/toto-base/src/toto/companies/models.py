"""A company is a community (2026-10-06, stage 65).

Nothing here makes a community a company: ``socialhub.Community.org_type``
has had ``company`` among its kinds all along, and that is the whole of the
type. This app adds what only a company has, in two tables keyed onto the
community, so an ordinary community carries no company field at all:

``CompanyRecord``
    the company's ID number, as text (leading zeros and the way it was
    typed are kept), one row per community;
``ShareHolding``
    how many whole shares one person holds in one company: one row per
    company and person, never negative. Both rules are the database's own.

It is NOT the old ``toto.company`` (toto-business): no company row beside
the community, no parties, no membership of its own, no share classes, no
ownership events, no departments, ledger, votes or documents. Members, the
head, the page, the bucket are the community's and stay so.

A holding names a ``people.Person`` and never a membership: joining or
leaving the community does not touch it, and holding shares gives nothing
of the community (``access``). Neither table is in the Django admin: every
change goes through the doors (``views``), which check who asks and write
the audit record.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

#: The longest ID number kept.
ID_NUMBER_MAX = 64


class CompanyRecord(models.Model):
    """What a company has and an ordinary community has not: its ID number.

    Separate from the community's database id and from its slug: the number
    a register, a court or a tax office gave the company. Text, because such
    numbers begin with zeros and carry dashes and letters.
    """

    community = models.OneToOneField(
        "socialhub.Community", on_delete=models.CASCADE, related_name="company_record")
    id_number = models.CharField(_("company ID number"), max_length=ID_NUMBER_MAX,
                                 blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = _("company record")
        verbose_name_plural = _("company records")

    def __str__(self):
        return f"company record of community {self.community_id}"


class ShareHolding(models.Model):
    """The whole shares one person holds in one company.

    CASCADE on both sides: the register is the company's and goes with it;
    the row is the person's data and goes with an erased account, as their
    memberships do.
    """

    community = models.ForeignKey(
        "socialhub.Community", on_delete=models.CASCADE, related_name="share_holdings")
    person = models.ForeignKey(
        "people.Person", on_delete=models.CASCADE, related_name="share_holdings")
    quantity = models.PositiveBigIntegerField(_("shares"), default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-quantity", "pk"]
        verbose_name = _("shareholding")
        verbose_name_plural = _("shareholdings")
        constraints = [
            models.UniqueConstraint(fields=["community", "person"],
                                    name="companies_one_holding_per_person"),
            models.CheckConstraint(condition=models.Q(quantity__gte=0),
                                   name="companies_holding_not_negative"),
        ]

    def __str__(self):
        return f"{self.quantity} shares of person {self.person_id} in community {self.community_id}"
