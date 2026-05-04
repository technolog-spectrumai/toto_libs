from toto.socialhub.models import Community as CommunitySql
from toto.socialhub.graph.models import Community as CommunityNode
from toto.socialhub.graph.models import Person as MemberNode
from toto.core.graph.models import Federation as FederationNode
from toto.locations.graph.models import Address as AddressNode
from toto.locations.graph.models import Territory as TerritoryNode
from toto.socialhub.models import Person as MemberSql
from toto.locations.graph.models import Address as AddressNode


class CommunityProjection:

    model = "Community"
    app = "socialhub"

    def sync_nodes(self):
        for c in CommunitySql.objects.all():
            node = CommunityNode.nodes.get_or_none(uuid=str(c.uid))

            if node and type(node) is not CommunityNode:
                node.delete()
                node = None

            if not node:
                node = CommunityNode(
                    uuid=str(c.uid),
                    name=c.name,
                    slug=c.slug,
                    org_type=c.org_type,
                    established_year=c.established_year,
                    is_autonomous=c.is_autonomous,
                    is_foreign=c.is_foreign,
                )
            else:
                node.name = c.name
                node.slug = c.slug
                node.org_type = c.org_type
                node.established_year = c.established_year
                node.is_autonomous = c.is_autonomous
                node.is_foreign = c.is_foreign

            node.save()

    def sync_edges(self):
        for c in CommunitySql.objects.all():
            gc = CommunityNode.nodes.get(uuid=str(c.uid))

            # Clear all relations
            gc.members.disconnect_all()
            gc.head.disconnect_all()
            gc.federation.disconnect_all()
            gc.location.disconnect_all()
            gc.territory.disconnect_all()

            # Members
            for m in c.members.all():
                gm = MemberNode.nodes.get_or_none(uuid=str(m.uid))
                if gm:
                    gc.members.connect(gm)

            # Head
            if c.head_id:
                head = MemberNode.nodes.get_or_none(uuid=str(c.head.uid))
                if head:
                    gc.head.connect(head)

            # Federation
            if c.federation_id:
                gf = FederationNode.nodes.get_or_none(uuid=str(c.federation.uid))
                if gf:
                    gc.federation.connect(gf)

            # Location (Address)
            if c.location_id:
                addr = AddressNode.nodes.get_or_none(uuid=str(c.location.uid))
                if addr:
                    gc.location.connect(addr)

            # Territory
            if c.territory_id:
                terr = TerritoryNode.nodes.get_or_none(uuid=str(c.territory.uid))
                if terr:
                    gc.territory.connect(terr)


class MemberProjection:

    model = "Person"
    app = "socialhub"

    def sync_nodes(self):
        for m in MemberSql.objects.all():
            node = MemberNode.nodes.get_or_none(uuid=str(m.uid))

            if node and type(node) is not MemberNode:
                node.delete()
                node = None

            if not node:
                node = MemberNode(
                    uuid=str(m.uid),
                    display_name=m.display_name,
                    email=m.email,
                    phone=m.phone,
                    date_of_birth=m.date_of_birth,
                )
            else:
                node.display_name = m.display_name
                node.email = m.email
                node.phone = m.phone
                node.date_of_birth = m.date_of_birth

            node.save()

    def sync_edges(self):
        for m in MemberSql.objects.all():
            gm = MemberNode.nodes.get_or_none(uuid=str(m.uid))
            if not gm:
                continue

            gm.patron.disconnect_all()
            gm.address.disconnect_all()

            # Patron
            if m.patron_id:
                patron = MemberNode.nodes.get_or_none(uuid=str(m.patron.uid))
                if patron:
                    gm.patron.connect(patron)

            # Address
            if m.address_id:
                addr = AddressNode.nodes.get_or_none(uuid=str(m.address.uid))
                if addr:
                    gm.address.connect(addr)
