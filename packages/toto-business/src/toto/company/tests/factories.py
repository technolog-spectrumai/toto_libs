"""Shared setup for the company tests. Irena's `IrenaModelFactoryMixin`, kept."""

from __future__ import annotations

from decimal import Decimal

from toto.company.models import Company, Party, ShareClass


class CompanyFactoryMixin:
    def make_company(self, name="Farfarele Brokker Inc.", **kwargs):
        return Company.objects.create(name=name, **kwargs)

    def make_party(self, company, name="Ada", **kwargs):
        return Party.objects.create(company=company, name=name, **kwargs)

    def make_share_class(self, company, name="Ordinary", slug="ordinary",
                         votes_per_unit=Decimal("1"), **kwargs):
        return ShareClass.objects.create(
            company=company, name=name, slug=slug,
            votes_per_unit=votes_per_unit, **kwargs,
        )
