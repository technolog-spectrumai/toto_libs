# projections.py

# =========================================================
# IMPORTS
# =========================================================

from toto.locations.models import (
    Address as AddressSql,
    MapLayer as MapLayerSql,
    MapLayerPolygon as MapLayerPolygonSql,
    RouteChain as RouteChainSql,
    Territory as TerritorySql,
    Zone as ZoneSql,
    Route as RouteSql,
)

from toto.locations.graph.models import (
    Address as AddressNode,
    MapLayer as MapLayerNode,
    MapLayerPolygon as MapLayerPolygonNode,
    RouteChain as RouteChainNode,
    Territory as TerritoryNode,
    Zone as ZoneNode,
    Route as RouteNode,
)

from toto.socialhub.graph.models import Person as PersonNode


# =========================================================
# BASE GEO PROJECTION
# =========================================================

class BaseGeoProjection:
    """
    Base class for GIS projections.
    Handles:
    - WKT conversion
    - JSON conversion
    - Node creation/update boilerplate
    - Field mapping
    """

    model = None
    app = "core"

    sql_model = None
    neo_model = None

    # Mapping: { neo_field: sql_field }
    field_map = {}

    def to_wkt(self, geom):
        return geom.wkt if geom else None

    def to_json(self, value):
        import json
        return json.dumps(value or {})

    def item_count(self):
        return self.sql_model.objects.count()

    def link_count(self):
        return 0

    def node_data_size(self):
        return len(self.field_map)

    def projection_stats(self):
        return {
            "items": self.item_count(),
            "links": self.link_count(),
            "node_data_size": self.node_data_size(),
        }

    def format_value(self, neo_field, value):
        if neo_field in ("geometry", "center"):
            return self.to_wkt(value)

        if neo_field in ("style", "properties"):
            return self.to_json(value)

        return value

    # -----------------------------
    # NODE SYNC shared
    # -----------------------------
    def sync_nodes(self):
        for obj in self.sql_model.objects.all():
            node = self.neo_model.nodes.get_or_none(uuid=str(obj.uid))

            # Replace wrong type
            if node and type(node) is not self.neo_model:
                node.delete()
                node = None

            # Create or update
            if not node:
                node = self.neo_model(uuid=str(obj.uid))

            # Apply field mapping
            for neo_field, sql_field in self.field_map.items():
                value = getattr(obj, sql_field)
                value = self.format_value(neo_field, value)
                setattr(node, neo_field, value)

            node.save()

    # -----------------------------
    # EDGE SYNC override in subclasses
    # -----------------------------
    def sync_edges(self):
        pass


# =========================================================
# ADDRESS PROJECTION
# =========================================================

class AddressProjection(BaseGeoProjection):
    model = "Address"
    app = "locations"

    sql_model = AddressSql
    neo_model = AddressNode

    field_map = {
        "country_name": "country_name",
        "state_or_province_name": "state_or_province_name",
        "locality_name": "locality_name",
        "street": "street",
        "building": "building",
        "apartment": "apartment",
        "geometry": "geometry",
    }

    def sync_edges(self):
        pass


# =========================================================
# TERRITORY PROJECTION
# =========================================================

class TerritoryProjection(BaseGeoProjection):
    model = "Territory"
    app = "locations"

    sql_model = TerritorySql
    neo_model = TerritoryNode

    field_map = {
        "name": "name",
        "geometry": "geometry",
    }

    def sync_edges(self):
        for territory in TerritorySql.objects.select_related("capital").all():
            territory_node = TerritoryNode.nodes.get(uuid=str(territory.uid))
            territory_node.capital.disconnect_all()

            if territory.capital_id:
                address_node = AddressNode.nodes.get_or_none(uuid=str(territory.capital.uid))
                if address_node:
                    territory_node.capital.connect(address_node)

    def link_count(self):
        return TerritorySql.objects.filter(capital__isnull=False).count()


# =========================================================
# ZONE PROJECTION
# =========================================================

class ZoneProjection(BaseGeoProjection):
    model = "Zone"
    app = "locations"

    sql_model = ZoneSql
    neo_model = ZoneNode

    field_map = {
        "name": "name",
        "geometry": "geometry",
    }

    def sync_edges(self):
        for zone in ZoneSql.objects.select_related("territory").all():
            zone_node = ZoneNode.nodes.get(uuid=str(zone.uid))
            zone_node.territory.disconnect_all()

            if zone.territory_id:
                territory_node = TerritoryNode.nodes.get_or_none(uuid=str(zone.territory.uid))
                if territory_node:
                    zone_node.territory.connect(territory_node)

    def link_count(self):
        return ZoneSql.objects.filter(territory__isnull=False).count()


# =========================================================
# ROUTE CHAIN PROJECTION
# =========================================================

class RouteChainProjection(BaseGeoProjection):
    model = "RouteChain"
    app = "locations"

    sql_model = RouteChainSql
    neo_model = RouteChainNode

    field_map = {
        "name": "name",
        "description": "description",
    }

    def sync_edges(self):
        # Routes own the outgoing PART_OF_CHAIN edge.
        pass


# =========================================================
# ROUTE PROJECTION
# =========================================================

class RouteProjection(BaseGeoProjection):
    model = "Route"
    app = "locations"

    sql_model = RouteSql
    neo_model = RouteNode

    field_map = {
        "name": "name",
        "sequence": "sequence",
        "geometry": "geometry",
    }

    def sync_edges(self):
        for route in RouteSql.objects.select_related(
            "route_chain",
            "start_address",
            "end_address",
        ).all():
            route_node = RouteNode.nodes.get(uuid=str(route.uid))

            route_node.start_address.disconnect_all()
            route_node.end_address.disconnect_all()
            route_node.route_chain.disconnect_all()

            if route.route_chain_id:
                chain_node = RouteChainNode.nodes.get_or_none(uuid=str(route.route_chain.uid))
                if chain_node:
                    route_node.route_chain.connect(chain_node)

            if route.start_address_id:
                address_node = AddressNode.nodes.get_or_none(uuid=str(route.start_address.uid))
                if address_node:
                    route_node.start_address.connect(address_node)

            if route.end_address_id:
                address_node = AddressNode.nodes.get_or_none(uuid=str(route.end_address.uid))
                if address_node:
                    route_node.end_address.connect(address_node)

    def link_count(self):
        return (
            RouteSql.objects.filter(route_chain__isnull=False).count()
            + RouteSql.objects.filter(start_address__isnull=False).count()
            + RouteSql.objects.filter(end_address__isnull=False).count()
        )


# =========================================================
# MAP LAYER PROJECTION
# =========================================================

class MapLayerProjection(BaseGeoProjection):
    model = "MapLayer"
    app = "locations"

    sql_model = MapLayerSql
    neo_model = MapLayerNode

    field_map = {
        "name": "name",
        "slug": "slug",
        "description": "description",
        "unit": "unit",
        "min_value": "min_value",
        "max_value": "max_value",
        "style": "style",
        "inverted_importance": "inverted_importance",
        "half_range": "half_range",
        "is_active": "is_active",
    }

    def sync_edges(self):
        for layer in MapLayerSql.objects.select_related("owner").all():
            layer_node = MapLayerNode.nodes.get(uuid=str(layer.uid))
            layer_node.owner.disconnect_all()

            if layer.owner_id:
                person_node = PersonNode.nodes.get_or_none(uuid=str(layer.owner.uid))
                if person_node:
                    layer_node.owner.connect(person_node)

    def link_count(self):
        return MapLayerSql.objects.filter(owner__isnull=False).count()


# =========================================================
# MAP LAYER POLYGON PROJECTION
# =========================================================

class MapLayerPolygonProjection(BaseGeoProjection):
    model = "MapLayerPolygon"
    app = "locations"

    sql_model = MapLayerPolygonSql
    neo_model = MapLayerPolygonNode

    field_map = {
        "name": "name",
        "value": "value",
        "properties": "properties",
        "geometry": "geometry",
        "center": "center",
    }

    def sync_edges(self):
        for polygon in MapLayerPolygonSql.objects.select_related("layer").all():
            polygon_node = MapLayerPolygonNode.nodes.get(uuid=str(polygon.uid))
            polygon_node.layer.disconnect_all()

            if polygon.layer_id:
                layer_node = MapLayerNode.nodes.get_or_none(uuid=str(polygon.layer.uid))
                if layer_node:
                    polygon_node.layer.connect(layer_node)

    def link_count(self):
        return MapLayerPolygonSql.objects.filter(layer__isnull=False).count()