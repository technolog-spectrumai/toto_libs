from django.contrib.auth.models import User
from toto.models import SerializableModel
from django.db import models
from colorfield.fields import ColorField
from ravioli.models import CollectionType, RelationType


class BaseQuery(SerializableModel):
    """
    Minimal abstract base class for reusable query definitions.
    Contains only a name and a query string.
    """

    name = models.CharField(
        max_length=200,
        unique=True,
        help_text="Human-readable name of the query."
    )

    query = models.TextField(
        help_text="The query string to run (Cypher, SQL, API, etc.)."
    )

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self):
        return self.name


class CypherQuery(BaseQuery):
    """
    Represents a predefined Cypher query that can be reused in the graph explorer.
    """

    description = models.TextField(
        blank=True,
        help_text="Optional description of what this query does."
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
    code = models.TextField(help_text="Restricted Python code snippet")
    test_context = models.JSONField(blank=True, null=True, help_text="Optional JSON context for testing")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class NodeStyle(models.Model):
    """
    Visual style for nodes of a given CollectionType within a specific Graph.
    """
    cypher_query = models.ForeignKey(CypherQuery, related_name="node_styles", on_delete=models.CASCADE)
    collection_type = models.ForeignKey(
        CollectionType,
        related_name="styles",
        on_delete=models.CASCADE
    )
    color = ColorField(default="#000000")  # default blue
    size = models.PositiveIntegerField(default=20)  # node radius or similar

    class Meta:
        unique_together = ("cypher_query", "collection_type")

    def __str__(self):
        return f"NodeStyle({self.collection_type.name} in {self.cypher_query.name})"


class EdgeStyle(models.Model):
    """
    Visual style for edges of a given RelationType within a specific Graph.
    """
    cypher_query = models.ForeignKey(CypherQuery, related_name="edge_styles", on_delete=models.CASCADE )
    relation_type = models.ForeignKey(
        RelationType,
        related_name="styles",
        on_delete=models.CASCADE
    )

    color = ColorField(default="#000000")  # default grey
    size = models.PositiveIntegerField(default=2)  # stroke width

    class Meta:
        unique_together = ("cypher_query", "relation_type")

    def __str__(self):
        return f"EdgeStyle({self.relation_type.name} in {self.cypher_query.name})"



