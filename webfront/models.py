from django.db import models
from django.utils.text import slugify
from colorfield.fields import ColorField
from ravioli.models import CollectionType, RelationType
from vault.models import Bucket
from mandragora.models import LambdaNode


# ------------------------------------------------------------
# Base Page (abstract)
# ------------------------------------------------------------

class Page(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            slug = base
            counter = 1
            Model = self.__class__

            while Model.objects.filter(slug=slug).exists():
                counter += 1
                slug = f"{base}-{counter}"

            self.slug = slug

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


# ------------------------------------------------------------
# CypherQuery (pure query)
# ------------------------------------------------------------

class CypherQuery(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    query = models.TextField()

    def __str__(self):
        return self.name


# ------------------------------------------------------------
# Node & Edge Styles (per CypherQuery)
# ------------------------------------------------------------

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

    def __str__(self):
        return f"NodeStyle({self.collection_type})"


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

    def __str__(self):
        return f"EdgeStyle({self.relation_type})"


# ------------------------------------------------------------
# GraphWorkflow (inherits Page)
# ------------------------------------------------------------

class GraphWorkflow(Page):
    cypher_query = models.ForeignKey(
        CypherQuery,
        on_delete=models.CASCADE,
        related_name="graph_workflows"
    )

    lambda_node = models.ForeignKey(
        LambdaNode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    is_active = models.BooleanField(default=True)


# ------------------------------------------------------------
# DynamicPage (inherits Page)
# ------------------------------------------------------------

class DynamicPage(Page):
    cypher_query = models.ForeignKey(
        CypherQuery,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional Cypher query powering this page."
    )

    lambda_node = models.ForeignKey(
        LambdaNode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional lambda that renders widgets for this page."
    )

    is_active = models.BooleanField(default=True)


# ------------------------------------------------------------
# FileWorkflow (inherits Page)
# ------------------------------------------------------------

class FileWorkflow(Page):
    bucket = models.ForeignKey(Bucket, on_delete=models.CASCADE)

    lambda_node = models.ForeignKey(
        LambdaNode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    is_active = models.BooleanField(default=True)
