from toto.socialhub.models import Community, CommunityMember
from toto.socialhub.graph.models import Community as CommunityNode
from toto.socialhub.graph.models import CommunityMember as MemberNode


class GraphProjection:
    """
    projection layer:
    - sync_nodes(): create/update all nodes
    - sync_edges(): create/update all relationships
    - _sync_community(): helper for community node
    - _sync_member(): helper for member node
    """

    # ---------------------------------------------------------
    # HELPERS
    # ---------------------------------------------------------
    def _sync_community(self, c: Community):
        """Create or update a CommunityNode from SQL Community."""
        node, _ = CommunityNode.nodes.get_or_create(
            sql_id=c.id,
            defaults={
                "name": c.name,
                "slug": c.slug,
                "org_type": c.org_type,
                "established_year": c.established_year,
                "is_autonomous": c.is_autonomous,
                "is_foreign": c.is_foreign,
            }
        )

        node.name = c.name
        node.slug = c.slug
        node.org_type = c.org_type
        node.established_year = c.established_year
        node.is_autonomous = c.is_autonomous
        node.is_foreign = c.is_foreign
        node.save()

        return node

    def _sync_member(self, m: CommunityMember):
        """Create or update a MemberNode from SQL CommunityMember."""
        node, _ = MemberNode.nodes.get_or_create(
            sql_id=m.id,
            defaults={
                "display_name": m.display_name,
                "email": m.email,
                "phone": m.phone,
                "date_of_birth": m.date_of_birth,
            }
        )

        node.display_name = m.display_name
        node.email = m.email
        node.phone = m.phone
        node.date_of_birth = m.date_of_birth
        node.save()

        return node

    # ---------------------------------------------------------
    # PUBLIC: SYNC ALL NODES
    # ---------------------------------------------------------
    def sync_nodes(self):
        """Sync all Community and Member nodes."""
        for c in Community.objects.all():
            self._sync_community(c)

        for m in CommunityMember.objects.all():
            self._sync_member(m)

    # ---------------------------------------------------------
    # PUBLIC: SYNC ALL EDGES
    # ---------------------------------------------------------
    def sync_edges(self):
        """Sync all relationships between nodes."""
        # Community → Members + Head
        for c in Community.objects.all():
            gc = CommunityNode.nodes.get(sql_id=c.id)

            # Members
            for m in c.members.all():
                gm = MemberNode.nodes.get(sql_id=m.id)
                gc.members.connect(gm)

            # Head
            if c.head_id:
                head = MemberNode.nodes.get(sql_id=c.head_id)
                gc.head.connect(head)

        # Member → Patron
        for m in CommunityMember.objects.all():
            if m.patron_id:
                gm = MemberNode.nodes.get(sql_id=m.id)
                patron = MemberNode.nodes.get(sql_id=m.patron_id)
                gm.patron.connect(patron)
