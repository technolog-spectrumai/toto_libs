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
    conversion_rules = models.JSONField(
        blank=True,
        null=True,
        help_text="Rules for mapping Django model fields to this schema"
    )

    def convert_instance(self, instance):
        """
            Convert a Django model instance into a dictionary that matches
            this CollectionType's schema.

            The conversion is guided by the `conversion_rules` JSON field,
            which maps schema field names to Django model field names.

            Example conversion_rules:
                {
                    "title": "name",
                    "createdOn": "created_at",
                    "owner": "created_by_id"
                }

            Args:
                instance (models.Model): A Django model instance to be converted.

            Returns:
                dict: A dictionary where keys are schema field names and values
                      are taken from the corresponding fields of the model instance.

            Raises:
                ValueError: If no conversion_rules are defined for this CollectionType.
        """
        if not self.conversion_rules:
            raise ValueError("No conversion rules defined for this CollectionType")

        result = {}
        for schema_field, model_field in self.conversion_rules.items():
            value = getattr(instance, model_field, None)
            result[schema_field] = value
        return result

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
    Represents a relation type with a name and optional metadata.
    """
    name = models.CharField(max_length=100, unique=True)
    metadata = models.JSONField(blank=True, null=True)  # flexible key-value info
    conversion_rules = models.JSONField(
        blank=True,
        null=True,
        help_text="Rules for mapping Django model relations to this schema"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def convert_relation(self, source_instance, target_instance):
        """
            Convert a relation between two Django model instances into a dictionary
            that matches this RelationType's schema.

            The conversion is guided by the `conversion_rules` JSON field,
            which maps schema field names to either source or target model fields,
            or to literal values.

            Example conversion_rules:
                {
                    "relationLabel": "friendship",
                    "sourceId": "source.id",
                    "targetId": "target.id",
                    "since": "source.created_at"
                }

            Args:
                source_instance (models.Model): The Django model instance representing
                                                the source of the relation.
                target_instance (models.Model): The Django model instance representing
                                                the target of the relation.

            Returns:
                dict: A dictionary where keys are schema field names and values
                      are derived from source/target fields or literal values.

            Raises:
                ValueError: If no conversion_rules are defined for this RelationType.
        """
        if not self.conversion_rules:
            raise ValueError("No conversion rules defined for this RelationType")

        result = {}
        for schema_field, mapping in self.conversion_rules.items():
            # mapping can specify 'source.field' or 'target.field'
            if mapping.startswith("source."):
                field_name = mapping.split(".", 1)[1]
                value = getattr(source_instance, field_name, None)
            elif mapping.startswith("target."):
                field_name = mapping.split(".", 1)[1]
                value = getattr(target_instance, field_name, None)
            else:
                # fallback: treat mapping as a literal or metadata key
                value = mapping
            result[schema_field] = value
        return result


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
