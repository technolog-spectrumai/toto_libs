# projections.py

# =========================================================
# IMPORTS
# =========================================================

from toto.locations.models import (
    Address as AddressSql,
    Territory as TerritorySql,
    Route as RouteSql,
)

from toto.locations.graph.models import (
    Address as AddressNode,
    Territory as TerritoryNode,
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
                if "geometry" in neo_field:
                    value = self.to_wkt(value)
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
        "geometry": "geometry",
    }

    def sync_edges(self):
        for r in RouteSql.objects.all():
            gr = RouteNode.nodes.get(uuid=str(r.uid))

            gr.start_address.disconnect_all()
            gr.end_address.disconnect_all()

            if r.start_address_id:
                ga = AddressNode.nodes.get_or_none(uuid=str(r.start_address.uid))
                if ga:
                    gr.start_address.connect(ga)

            if r.end_address_id:
                ga = AddressNode.nodes.get_or_none(uuid=str(r.end_address.uid))
                if ga:
                    gr.end_address.connect(ga)
