from django.db import models
from django.contrib.auth.models import User
from toto.models import SerializableModel, BaseExecutableModel
from colorfield.fields import ColorField
from ravioli.models import CollectionType, RelationType
from django.utils.text import slugify


class BaseQuery(SerializableModel):
    """
    Abstract base class for reusable query definitions.
    Contains only a name and a query string.
    """

    name = models.CharField(
        max_length=200,
        unique=True,
        help_text="Human-readable name."
    )

    query = models.TextField(
        help_text="The query string to run (Cypher, SQL, API, etc.)."
    )

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self):
        return self.name


class BaseExecutableQuery(BaseExecutableModel, BaseQuery):
    """
    Combines query + executable code.
    Shared by CypherQuery and DynamicPage.
    """

    class Meta:
        abstract = True
        ordering = ["name"]


# ---------------------------------------------------------
# Cypher Query
# ---------------------------------------------------------

class CypherQuery(BaseExecutableQuery):
    """
    Represents a predefined Cypher query that can be reused in the graph explorer.
    """

    description = models.TextField(blank=True)

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cypher_queries"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]


# ---------------------------------------------------------
# Styles
# ---------------------------------------------------------

class NodeStyle(models.Model):
    cypher_query = models.ForeignKey(
        CypherQuery,
        related_name="node_styles",
        on_delete=models.CASCADE
    )
    collection_type = models.ForeignKey(
        CollectionType,
        related_name="styles",
        on_delete=models.CASCADE
    )
    color = ColorField(default="#000000")
    size = models.PositiveIntegerField(default=20)

    class Meta:
        unique_together = ("cypher_query", "collection_type")


class EdgeStyle(models.Model):
    cypher_query = models.ForeignKey(
        CypherQuery,
        related_name="edge_styles",
        on_delete=models.CASCADE
    )
    relation_type = models.ForeignKey(
        RelationType,
        related_name="styles",
        on_delete=models.CASCADE
    )
    color = ColorField(default="#000000")
    size = models.PositiveIntegerField(default=2)

    class Meta:
        unique_together = ("cypher_query", "relation_type")



class DynamicPage(BaseExecutableQuery):
    name = models.CharField(max_length=200, unique=True)

    slug = models.SlugField(
        max_length=200,
        unique=True,
        blank=True,
        help_text="URL-friendly identifier for this page."
    )

    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dynamic_pages",
        help_text="Owner of this page."
    )

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        # Auto-generate slug if missing
        if not self.slug:
            base = slugify(self.name)
            slug = base
            counter = 1

            while DynamicPage.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{counter}"
                counter += 1

            self.slug = slug

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

