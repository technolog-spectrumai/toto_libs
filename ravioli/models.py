from typing import List
from django.contrib.auth.models import User
from toto.models import SerializableModel
from django.db import models
from .graph import GraphTranslator
from django.utils.timezone import now


class CypherQuery(SerializableModel):
    """
    Represents a predefined Cypher query that can be reused in the graph explorer.
    """

    name = models.CharField(
        max_length=200,
        unique=True,
        help_text="Human-readable name of the query (e.g. 'All Nodes and Relationships')."
    )
    description = models.TextField(
        blank=True,
        help_text="Optional description of what this query does."
    )
    query = models.TextField(
        help_text="The Cypher query string to run against Neo4j."
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cypher_queries",
        help_text="User who created this query."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @staticmethod
    def for_node_types(node_types: List[str], created_by: User = None) -> "CypherQuery":
        """
        Factory method: build a CypherQuery that fetches all nodes of given types
        and their one-hop neighbours.
        """
        if not node_types:
            raise ValueError("You must provide at least one node type.")

        # Build label string like ":Person|Company|Product"
        label_expr = ":`" + "`|:`".join(node_types) + "`"

        cypher = f"""
        MATCH (n{label_expr})-[r]-(m)
        RETURN n, r, m
        LIMIT 100
        """

        return CypherQuery(
            name=f"{', '.join(node_types)} with one-hop neighbours",
            description=f"Fetch all {', '.join(node_types)} nodes and their immediate neighbours.",
            query=cypher.strip(),
            created_by=created_by,
            is_active=True,
        )


class Graph(models.Model):
    """
    Represents a whole graph (like a sheet or canvas).
    Groups nodes and edges together under one namespace.
    """
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True, null=True)

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="graphs"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def export_to_neo4j(self):
        translator = GraphTranslator(self)
        return translator.export()


class CollectionType(models.Model):
    """
    Represents a node type with schema and form layout.
    """
    name = models.CharField(max_length=100, unique=True)
    json_schema = models.JSONField()       # schema definition for node data
    form_layout = models.JSONField()       # UI layout for forms

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class DataNode(models.Model):
    """
    Represents a node in a graph, linked to a CollectionType.
    """
    name = models.CharField(max_length=100, unique=True)
    data = models.JSONField(blank=True, null=True)  # node-specific data
    collection_type = models.ForeignKey(
        CollectionType,
        related_name="nodes",
        on_delete=models.CASCADE
    )
    graph = models.ForeignKey(
        Graph,
        related_name="nodes",
        on_delete=models.CASCADE
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.collection_type.name})"


class RelationType(models.Model):
    """
    Represents a relation type with schema and form layout.
    """
    name = models.CharField(max_length=100, unique=True)
    json_schema = models.JSONField()       # schema definition for relation metadata
    form_layout = models.JSONField()       # UI layout for relation forms

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class DataEdge(models.Model):
    """
    Represents a directed edge between two nodes, linked to a RelationType.
    """
    source = models.ForeignKey(
        DataNode,
        related_name="outgoing_edges",
        on_delete=models.CASCADE
    )
    target = models.ForeignKey(
        DataNode,
        related_name="incoming_edges",
        on_delete=models.CASCADE
    )
    label = models.CharField(max_length=100, blank=True, null=True)
    metadata = models.JSONField(blank=True, null=True)  # edge-specific metadata
    relation_type = models.ForeignKey(
        RelationType,
        related_name="edges",
        on_delete=models.CASCADE
    )
    graph = models.ForeignKey(
        Graph,
        related_name="edges",
        on_delete=models.CASCADE
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("source", "target", "label", "relation_type")

    def __str__(self):
        return f"{self.source} -> {self.target} [{self.relation_type.name}] ({self.label})"


class GraphProposal(models.Model):
    """
    Proposal for building or extending a Graph.
    Stores JSON config with nodes and relations referencing existing CollectionTypes.
    Requires human approval before applying.
    """
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True, null=True)

    # JSON config: nodes + relations referencing existing types
    config = models.JSONField(
        help_text="Draft proposal config with nodes and relations referencing CollectionTypes"
    )

    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        default="pending"
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="graph_proposals"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_graph_proposals"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"GraphProposal: {self.name} ({self.status})"

    def approve(self, reviewer: User):
        """
        Apply the proposal: create DataNodes and DataEdges
        using existing CollectionTypes and RelationTypes.
        """

        graph = Graph.objects.get(pk=self.config["graph_id"])

        # Create nodes referencing existing CollectionTypes
        for node in self.config.get("nodes", []):
            collection = CollectionType.objects.get(name=node["type"])
            DataNode.objects.create(
                name=node["name"],
                data=node.get("data", {}),
                collection_type=collection,
                graph=graph
            )

        # Create relation types + edges
        for rt in self.config.get("relations", []):
            relation, _ = RelationType.objects.get_or_create(
                name=rt["name"],
                defaults={
                    "json_schema": rt.get("json_schema", {"relation": "unspecified"}),
                    "form_layout": rt.get("form_layout", {"layout": "auto"})
                }
            )
            for edge in rt.get("edges", []):
                source = DataNode.objects.get(name=edge["source"], graph=graph)
                target = DataNode.objects.get(name=edge["target"], graph=graph)
                DataEdge.objects.create(
                    source=source,
                    target=target,
                    relation_type=relation,
                    graph=graph,
                    label=edge.get("label"),
                    metadata=edge.get("metadata", {})
                )

        self.status = "approved"
        self.reviewed_by = reviewer
        self.reviewed_at = now()
        self.save()
