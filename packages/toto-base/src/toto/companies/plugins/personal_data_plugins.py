"""Companies in "Download my data" (stage 65): the shares recorded in the
member's name, company by company. Every row of theirs, also in a community
that is no company any more: the row is their data wherever it is shown."""

from toto.core.personal_data import PersonalDataPlugin, Table


@PersonalDataPlugin.plugin(key="companies")
class CompaniesData(PersonalDataPlugin):
    def tables(self, user) -> list[Table]:
        from toto.companies.models import ShareHolding
        from toto.people.models import Person

        person = Person.objects.filter(user=user).first()
        rows = []
        if person is not None:
            for holding in (ShareHolding.objects.filter(person=person)
                            .select_related("community").order_by("community__name", "pk")):
                rows.append({"company": holding.community.name,
                             "company_slug": holding.community.slug,
                             "shares": holding.quantity,
                             "recorded": holding.created_at.isoformat(),
                             "changed": holding.updated_at.isoformat()})
        return [Table("companies_shareholdings",
                      "The shares recorded in your name in a company's register: "
                      "the company and how many.",
                      rows)]
