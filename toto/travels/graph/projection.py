from toto.travels.models import Travel as TravelSql, Visit as VisitSql

from toto.locations.graph.models import (
    Route as RouteNode,
    Address as AddressNode,
    Travel as TravelNode,
    Visit as VisitNode,
)
from toto.socialhub.graph.models import Person as PersonNode

from toto.locations.graph.projection import BaseGeoProjection


class TravelProjection(BaseGeoProjection):
    model = "Travel"
    app = "travels"

    sql_model = TravelSql
    neo_model = TravelNode

    field_map = {
        "info": "info",
        "starts_at": "starts_at",
        "ends_at": "ends_at",
    }

    def sync_edges(self):
        for travel in TravelSql.objects.select_related("route").prefetch_related("participants").all():
            travel_node = TravelNode.nodes.get(uuid=str(travel.uid))

            travel_node.route.disconnect_all()
            travel_node.participants.disconnect_all()

            if travel.route_id:
                route_node = RouteNode.nodes.get_or_none(uuid=str(travel.route.uid))
                if route_node:
                    travel_node.route.connect(route_node)

            for participant in travel.participants.all():
                person_node = PersonNode.nodes.get_or_none(uuid=str(participant.uid))
                if person_node:
                    travel_node.participants.connect(person_node)

    def link_count(self):
        route_links = TravelSql.objects.filter(route__isnull=False).count()
        participant_links = sum(
            travel.participants.count()
            for travel in TravelSql.objects.prefetch_related("participants").all()
        )
        return route_links + participant_links


class VisitProjection(BaseGeoProjection):
    model = "Visit"
    app = "travels"

    sql_model = VisitSql
    neo_model = VisitNode

    field_map = {
        "review": "review",
        "score": "score",
    }

    def sync_edges(self):
        for visit in VisitSql.objects.select_related("participant", "location").all():
            visit_node = VisitNode.nodes.get(uuid=str(visit.uid))

            visit_node.participant.disconnect_all()
            visit_node.location.disconnect_all()

            if visit.participant_id:
                person_node = PersonNode.nodes.get_or_none(uuid=str(visit.participant.uid))
                if person_node:
                    visit_node.participant.connect(person_node)

            if visit.location_id:
                address_node = AddressNode.nodes.get_or_none(uuid=str(visit.location.uid))
                if address_node:
                    visit_node.location.connect(address_node)

    def link_count(self):
        return (
            VisitSql.objects.filter(participant__isnull=False).count()
            + VisitSql.objects.filter(location__isnull=False).count()
        )
