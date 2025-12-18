from typing import List
from django.conf import settings
from django.contrib.auth.models import User
from toto.models import SerializableModel
from django.db import models
from .graph import GraphTranslator
from django.apps import apps


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
    conversion_rules = models.JSONField(default=dict, blank=True)

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
    Represents a relation type with a name and optional metadata.
    """
    name = models.CharField(max_length=100, unique=True)
    metadata = models.JSONField(blank=True, null=True)  # flexible key-value info

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

