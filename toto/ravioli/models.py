from django.db import models


class CypherQuery(models.Model):
    name = models.CharField(max_length=200)
    query = models.TextField()
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "Cypher Query"
        verbose_name_plural = "Cypher Queries"

    def __str__(self):
        return self.name


class CypherQueryResult(models.Model):
    query = models.ForeignKey(CypherQuery, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "Graph Viewer"
        verbose_name_plural = "Graph Viewer"


class GraphSync(models.Model):
    """
    Dummy model used only to expose a global admin action
    for syncing SQL → Neo4j graph.
    """
    class Meta:
        verbose_name = "Graph Sync"
        verbose_name_plural = "Graph Sync"
        managed = False  # no DB table

    def __str__(self):
        return "Graph Sync"
