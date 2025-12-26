from django.contrib.auth.models import User
from toto.models import SerializableModel
from .translate import GraphTranslator
from django.conf import settings
from django.db import models


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


class CollectionType(models.Model):  # Node type
    """
    Represents a node type with schema and form layout.
    """
    name = models.CharField(max_length=100, unique=True)
    json_schema = models.JSONField(null=True, blank=True)
    form_layout = models.JSONField(null=True, blank=True)

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


class RelationType(models.Model): # Edge type
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


def get_graph_allowed_app_choices():
    apps = getattr(settings, "GRAPH_ALLOWED_APPS", [])
    return [(app, app) for app in apps] if apps else []


class Collector(models.Model):
    """
    Defines how an external app maps its data into this graph system.
    Stores JSON mappings for nodes and edges.
    """

    app_name = models.CharField(
        max_length=150,
        unique=True,
        choices=get_graph_allowed_app_choices(),
        help_text="Name of the external app or integration."
    )

    node_map = models.JSONField(
        blank=True,
        null=True,
        help_text="JSON mapping describing how external node data maps to CollectionTypes."
    )

    edge_map = models.JSONField(
        blank=True,
        null=True,
        help_text="JSON mapping describing how external edge data maps to RelationTypes."
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.app_name


