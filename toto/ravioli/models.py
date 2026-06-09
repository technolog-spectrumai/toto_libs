from django.db import models


class CypherQuery(models.Model):
    name = models.CharField(max_length=200)
    query = models.TextField()
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "Knowledge Graph Query"
        verbose_name_plural = "Knowledge Graph Queries"

    def __str__(self):
        return self.name


class CypherQueryResult(models.Model):
    query = models.ForeignKey(CypherQuery, on_delete=models.CASCADE, related_name="results")
    result_nodes = models.JSONField(null=True, blank=True)
    result_edges = models.JSONField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        verbose_name = "Graph Viewer"
        verbose_name_plural = "Graph Viewer"


# NOTE: the SQL→Neo4j sync models (GraphChangeEvent, GraphProjectionPlan,
# GraphSync, GraphSyncSchedule) now live in the toto.sql_neo4j_sync app.
