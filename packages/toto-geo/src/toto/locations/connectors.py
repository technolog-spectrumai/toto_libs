from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from toto.core.connectors import (
    ReadOnlyModelConnector,
    filter_search,
    geometry_to_json,
    get_object_by_config,
    minimal_named,
    minimal_person,
    register_connector,
)


@register_connector
class LocationsReadConnector(ReadOnlyModelConnector):
    """Reads locations as nobody (map domains, 2026-09-30): a workflow holds
    no clearance, so an item kept to one is not its to list or fetch, a route
    chain counts its open routes only, and a hidden end or capital is left
    out."""

    connector_type = "locations_read"
    label = _("Locations read")
    app_label = "locations"
    allowed_resources = {"address", "territory", "route_chain", "route", "map_layer"}

    def validate(self) -> list[str]:
        errors = super().validate()
        resource = self.config.get("resource", "address")
        if resource not in self.allowed_resources:
            errors.append(
                f"Connector resource must be one of: {', '.join(sorted(self.allowed_resources))}."
            )
        return errors

    def execute(self, input_data: dict | None = None) -> dict:
        input_data = input_data or {}
        resource = self.config.get("resource", "address")

        if resource == "address":
            return self._execute_address(input_data)
        if resource == "territory":
            return self._execute_territory(input_data)
        if resource == "route_chain":
            return self._execute_route_chain(input_data)
        if resource == "route":
            return self._execute_route(input_data)
        if resource == "map_layer":
            return self._execute_map_layer(input_data)

        raise ValueError(f"Unsupported locations resource: {resource}")

    def _execute_address(self, input_data: dict) -> dict:
        from toto.locations.access import readable_addresses

        qs = readable_addresses(None)
        if self.config.get("action", "list") == "get":
            address = get_object_by_config(qs, self, input_data, default_lookup="id")
            return {"data": {"address": serialize_address(address)}}
        query = self.query_value(input_data)
        qs = filter_search(
            qs,
            self,
            input_data,
            Q(street__icontains=query)
            | Q(locality_name__icontains=query)
            | Q(state_or_province_name__icontains=query)
            | Q(country_name__icontains=query),
        )
        return {"data": {"addresses": [serialize_address(item) for item in qs.order_by("locality_name", "street")[:self.limit()]]}}

    def _execute_territory(self, input_data: dict) -> dict:
        from toto.locations.access import readable_territories

        qs = readable_territories(None).select_related("capital")
        if self.config.get("action", "list") == "get":
            territory = get_object_by_config(qs, self, input_data, default_lookup="id")
            return {"data": {"territory": serialize_territory(territory)}}
        query = self.query_value(input_data)
        qs = filter_search(qs, self, input_data, Q(name__icontains=query))
        return {"data": {"territories": [serialize_territory(item) for item in qs.order_by("name")[:self.limit()]]}}

    def _execute_route_chain(self, input_data: dict) -> dict:
        from toto.locations.models import RouteChain

        qs = RouteChain.objects.all()
        if self.config.get("action", "list") == "get":
            chain = get_object_by_config(qs, self, input_data, default_lookup="id")
            return {"data": {"route_chain": serialize_route_chain(chain)}}
        query = self.query_value(input_data)
        qs = filter_search(qs, self, input_data, Q(name__icontains=query) | Q(description__icontains=query))
        return {"data": {"route_chains": [serialize_route_chain(item) for item in qs.order_by("name")[:self.limit()]]}}

    def _execute_route(self, input_data: dict) -> dict:
        from toto.locations.models import Route

        from toto.locations.access import readable_routes

        qs = readable_routes(None, Route.objects.select_related("route_chain", "start_address", "end_address"))
        if self.config.get("action", "list") == "get":
            route = get_object_by_config(qs, self, input_data, default_lookup="id")
            return {"data": {"route": serialize_route(route)}}
        query = self.query_value(input_data)
        qs = filter_search(qs, self, input_data, Q(name__icontains=query) | Q(route_chain__name__icontains=query))
        return {"data": {"routes": [serialize_route(item) for item in qs.order_by("route_chain__name", "sequence")[:self.limit()]]}}

    def _execute_map_layer(self, input_data: dict) -> dict:
        from toto.locations.models import MapLayer

        from toto.locations.access import readable_layers

        qs = readable_layers(None, MapLayer.objects.select_related("owner"))
        if self.config.get("action", "list") == "get":
            layer = get_object_by_config(qs, self, input_data, default_lookup="slug")
            return {"data": {"map_layer": serialize_map_layer(layer)}}
        query = self.query_value(input_data)
        qs = filter_search(qs, self, input_data, Q(name__icontains=query) | Q(slug__icontains=query))
        return {"data": {"map_layers": [serialize_map_layer(item) for item in qs.order_by("name")[:self.limit()]]}}


def serialize_address(address) -> dict:
    return {
        "id": address.id,
        "uid": str(address.uid),
        "label": str(address),
        "country_name": address.country_name,
        "state_or_province_name": address.state_or_province_name,
        "locality_name": address.locality_name,
        "street": address.street,
        "building": address.building,
        "apartment": address.apartment,
        "geometry": geometry_to_json(address.geometry),
    }


def _open(obj):
    """``obj`` when it is in no kept map domain (what nobody may read), else None."""
    from toto.locations.access import readable_or_none

    return readable_or_none(None, obj)


def serialize_territory(territory) -> dict:
    capital = _open(territory.capital)
    return {
        "id": territory.id,
        "uid": str(territory.uid),
        "name": territory.name,
        "capital": serialize_address(capital) if capital else None,
        "geometry": geometry_to_json(territory.geometry),
    }


def serialize_route_chain(chain) -> dict:
    return {
        "id": chain.id,
        "uid": str(chain.uid),
        "name": chain.name,
        "description": chain.description,
        "route_count": _open_routes(chain.routes.all()).count(),
    }


def _open_routes(queryset):
    from toto.locations.access import readable_routes

    return readable_routes(None, queryset)


def serialize_route(route) -> dict:
    start, end = _open(route.start_address), _open(route.end_address)
    return {
        "id": route.id,
        "uid": str(route.uid),
        "name": route.name,
        "sequence": route.sequence,
        "route_chain": minimal_named(route.route_chain) if route.route_chain else None,
        "start_address": serialize_address(start) if start else None,
        "end_address": serialize_address(end) if end else None,
        "geometry": geometry_to_json(route.geometry),
    }


def serialize_map_layer(layer) -> dict:
    return {
        "id": layer.id,
        "uid": str(layer.uid),
        "name": layer.name,
        "slug": layer.slug,
        "description": layer.description,
        "unit": layer.unit,
        "min_value": layer.min_value,
        "max_value": layer.max_value,
        "style": layer.style,
        "is_active": layer.is_active,
        "owner": minimal_person(layer.owner) if layer.owner else None,
    }
