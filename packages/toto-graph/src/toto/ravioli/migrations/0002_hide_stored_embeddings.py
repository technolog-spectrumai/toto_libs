"""Stored query results lose their embeddings (2026-10-12).

Every ``CypherQueryResult`` saved before ``toto.ravioli.props`` holds each node's and
relationship's full properties, vectors included. The readers scrub them on the way
out, but the rows themselves are the "stored query results" a person or an export can
still reach: rewrite them once through the same rule. Only these JSON rows change —
nothing in Neo4j does. Not reversible, and nothing to reverse: the vectors are derived
data and stay in the graph.
"""
from django.db import migrations


def hide_embeddings(apps, schema_editor):
    from toto.ravioli.props import visible_items

    CypherQueryResult = apps.get_model("ravioli", "CypherQueryResult")
    for row in CypherQueryResult.objects.all().iterator():
        nodes = visible_items(row.result_nodes) if row.result_nodes is not None else None
        edges = visible_items(row.result_edges) if row.result_edges is not None else None
        if nodes != row.result_nodes or edges != row.result_edges:
            row.result_nodes, row.result_edges = nodes, edges
            row.save(update_fields=["result_nodes", "result_edges"])


class Migration(migrations.Migration):

    dependencies = [
        ("ravioli", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(hide_embeddings, migrations.RunPython.noop),
    ]
