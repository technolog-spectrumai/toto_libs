from toto.core.models import Federation as FederationSql
from toto.core.graph.models import Federation as FederationNode


class FederationProjection:
    """
    Sync ONLY Federation nodes.
    No communities.
    No edges.
    """

    # ---------------------------------------------------------
    # NODE SYNC
    # ---------------------------------------------------------
    def _sync_federation(self, f: FederationSql):
        node = FederationNode.nodes.get_or_none(uuid=str(f.uid))

        # If node exists but is wrong type, replace it
        if node and type(node) is not FederationNode:
            node.delete()
            node = None

        # Create or update
        if not node:
            node = FederationNode(
                uuid=str(f.uid),
                name=f.name,
                description=f.description,
                logo=f.logo.url if f.logo else None,
                created_at=f.created_at,
                active=f.active,
            )
        else:
            node.name = f.name
            node.description = f.description
            node.logo = f.logo.url if f.logo else None
            node.created_at = f.created_at
            node.active = f.active

        node.save()
        return node

    # ---------------------------------------------------------
    # PUBLIC: SYNC ALL FEDERATION NODES
    # ---------------------------------------------------------
    def sync_nodes(self):
        for f in FederationSql.objects.all():
            self._sync_federation(f)

    # ---------------------------------------------------------
    # PUBLIC: SYNC EDGES (EMPTY — NO COMMUNITIES ANYMORE)
    # ---------------------------------------------------------
    def sync_edges(self):
        # Nothing to sync — Federation has no relationships now
        return
