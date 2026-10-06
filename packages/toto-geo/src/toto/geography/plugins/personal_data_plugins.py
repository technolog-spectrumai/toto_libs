"""Geography in "Download my data" (2026-10-06): the member's own point,
its name and its note."""

from toto.core.personal_data import PersonalDataPlugin, Table


@PersonalDataPlugin.plugin(key="geography")
class GeographyData(PersonalDataPlugin):
    def tables(self, user) -> list[Table]:
        from toto.geography.erasure import export_rows
        from toto.people.models import Person

        person = Person.objects.filter(user=user).first()
        return [Table("geography_address",
                      "Your point on the map: where it is, its name and its note.",
                      export_rows(user, person))]
