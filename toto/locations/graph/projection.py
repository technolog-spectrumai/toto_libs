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


# =========================================================
# BASE GEO PROJECTION
# =========================================================

class BaseGeoProjection:
    """
    Base class for GIS projections.
    Handles:
    - WKT conversion
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

    # -----------------------------
    # NODE SYNC (shared)
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
                if neo_field in ("geometry", "center"):
                    value = self.to_wkt(value)
                elif neo_field in ("style", "properties"):
                    value = self.to_json(value)
                setattr(node, neo_field, value)

            node.save()

    # -----------------------------
    # EDGE SYNC (override in subclasses)
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
        # Address has no outgoing edges
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
        for t in TerritorySql.objects.all():
            gt = TerritoryNode.nodes.get(uuid=str(t.uid))
            gt.capital.disconnect_all()

            if t.capital_id:
                ga = AddressNode.nodes.get_or_none(uuid=str(t.capital.uid))
                if ga:
                    gt.capital.connect(ga)

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
        for z in ZoneSql.objects.select_related("territory").all():
            gz = ZoneNode.nodes.get(uuid=str(z.uid))
            gz.territory.disconnect_all()

            if z.territory_id:
                gt = TerritoryNode.nodes.get_or_none(uuid=str(z.territory.uid))
                if gt:
                    gz.territory.connect(gt)

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
        for r in RouteSql.objects.all():
            gr = RouteNode.nodes.get(uuid=str(r.uid))

            gr.start_address.disconnect_all()
            gr.end_address.disconnect_all()
            gr.route_chain.disconnect_all()

            if r.route_chain_id:
                gc = RouteChainNode.nodes.get_or_none(uuid=str(r.route_chain.uid))
                if gc:
                    gr.route_chain.connect(gc)

            if r.start_address_id:
                ga = AddressNode.nodes.get_or_none(uuid=str(r.start_address.uid))
                if ga:
                    gr.start_address.connect(ga)

            if r.end_address_id:
                ga = AddressNode.nodes.get_or_none(uuid=str(r.end_address.uid))
                if ga:
                    gr.end_address.connect(ga)

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
        # MapLayerPolygon owns the outgoing IN_LAYER edge.
        pass


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
            gp = MapLayerPolygonNode.nodes.get(uuid=str(polygon.uid))
            gp.layer.disconnect_all()

            gl = MapLayerNode.nodes.get_or_none(uuid=str(polygon.layer.uid))
            if gl:
                gp.layer.connect(gl)

    def link_count(self):
        return MapLayerPolygonSql.objects.filter(layer__isnull=False).count()
