"""Geography in "Download my data" (2026-10-06): the member's own point,
its name and its note; since stage 64 also the pins and zones they saved for
a community and the comments they wrote under pins and zones."""

from toto.core.personal_data import PersonalDataPlugin, Table


@PersonalDataPlugin.plugin(key="geography")
class GeographyData(PersonalDataPlugin):
    def tables(self, user) -> list[Table]:
        from toto.geography.erasure import contribution_rows, export_rows
        from toto.people.models import Person

        person = Person.objects.filter(user=user).first()
        mine = contribution_rows(user)
        return [
            Table("geography_address",
                  "Your point on the map: where it is, its name and its note.",
                  export_rows(user, person)),
            Table("geography_pins",
                  "The pins you saved for a community: where, name, address and note.",
                  mine["pins"]),
            Table("geography_zones",
                  "The zones you saved for a community: name, description and outline.",
                  mine["zones"]),
            Table("geography_comments",
                  "The comments you wrote under community pins and zones.",
                  mine["comments"]),
        ]
