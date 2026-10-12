"""The map in "Download my data" (2026-10-12): the member's home on the map
and how they share it, the addresses they added and the routes they saved.
Their own rows alone; another person's address is never among them."""

from toto.core.personal_data import PersonalDataPlugin, Table


def _address(address) -> dict:
    from toto.locations.links import point_of

    point = point_of(address) or (None, None)
    return {"address": str(address), "latitude": point[0], "longitude": point[1],
            "note": address.note}


@PersonalDataPlugin.plugin(key="locations")
class LocationsData(PersonalDataPlugin):
    def tables(self, user) -> list[Table]:
        from toto.locations.models import Address, Home, Route

        home = (Home.objects.filter(person__user=user, address__isnull=False)
                .select_related("address").first())
        homes = [dict(_address(home.address), shown_to_others=home.sharing)] if home else []
        added = [_address(address)
                 for address in Address.objects.filter(created_by=user).order_by("pk")]
        routes = [{"name": str(route),
                   "from": str(route.start_address) if route.start_address_id else "",
                   "to": str(route.end_address) if route.end_address_id else "",
                   "notes": route.notes}
                  for route in Route.objects.filter(created_by=user)
                  .select_related("start_address", "end_address").order_by("pk")]
        return [
            Table("locations_home", "Where you live on the map, and what of it others see "
                  "(off, approximate or exact).", homes),
            Table("locations_addresses", "The addresses you added to the map.", added),
            Table("locations_routes", "The routes you saved on the map.", routes),
        ]
