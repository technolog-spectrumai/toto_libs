from django.utils import timezone

from toto.workflows.predefined_tasks import register


@register("ravioli_generate_plan")
def ravioli_generate_plan(input_data: dict) -> dict:
    from .connection import Neo4jClient, is_enabled
    from .loader import load_all_configs, validate_configs
    from .planner import create_projection_plan as build_plan

    if not is_enabled():
        raise RuntimeError("RAVIOLI_ENABLED is False — cannot connect to Neo4j.")

    configs = load_all_configs()
    errors = validate_configs(configs)
    if errors:
        raise RuntimeError("Graph config invalid: " + "; ".join(errors))

    labels = (input_data.get("data") or {}).get("labels") or None
    client = Neo4jClient()
    try:
        plan = build_plan(client, labels=labels, configs=configs)
    finally:
        client.close()

    return {"data": {"plan_id": plan.pk, "total_changes": plan.total_changes}}


@register("ravioli_apply_plan")
def ravioli_apply_plan(input_data: dict) -> dict:
    from .connection import Neo4jClient, is_enabled
    from .models import GraphProjectionPlan
    from .planner import apply_projection_plan

    if not is_enabled():
        raise RuntimeError("RAVIOLI_ENABLED is False — cannot connect to Neo4j.")

    plan_id = (input_data.get("data") or {}).get("plan_id")
    if plan_id is None:
        raise ValueError("ravioli_apply_plan requires plan_id in input data from previous node.")

    plan = GraphProjectionPlan.objects.get(pk=plan_id)
    if plan.status != GraphProjectionPlan.STATUS_READY:
        raise RuntimeError(f"Plan #{plan_id} is not ready (status: {plan.status}).")

    client = Neo4jClient()
    try:
        apply_projection_plan(client, plan)
    finally:
        client.close()

    return {"data": {"plan_id": plan.pk, "status": plan.status, "total_changes": plan.total_changes}}


@register("ravioli_run_cypher_query")
def ravioli_run_cypher_query(input_data: dict) -> dict:
    from .connection import Neo4jClient, is_enabled
    from .models import CypherQuery, CypherQueryResult

    if not is_enabled():
        raise RuntimeError("RAVIOLI_ENABLED is False — cannot connect to Neo4j.")

    query_id = (input_data.get("data") or {}).get("query_id")
    if query_id is None:
        raise ValueError("ravioli_run_cypher_query requires query_id in input data.")

    query = CypherQuery.objects.get(pk=query_id)
    client = Neo4jClient()
    try:
        records = client.run_cypher(query.query)
        nodes, edges = client.extract_graph(records)
    finally:
        client.close()

    CypherQueryResult.objects.update_or_create(
        query=query,
        defaults={
            "result_nodes": nodes,
            "result_edges": edges,
            "last_run_at": timezone.now(),
            "error": "",
        },
    )

    return {"data": {"query_id": query_id, "node_count": len(nodes), "edge_count": len(edges)}}


@register("ravioli_clear_db")
def ravioli_clear_db(input_data: dict) -> dict:
    from .connection import Neo4jClient, is_enabled

    if not is_enabled():
        raise RuntimeError("RAVIOLI_ENABLED is False — cannot connect to Neo4j.")

    client = Neo4jClient()
    try:
        # Count first — can't reference deleted entities in RETURN.
        rel_count = client.run_cypher("MATCH ()-[r]->() RETURN count(r) AS cnt")[0]["cnt"]
        node_count = client.run_cypher("MATCH (n) RETURN count(n) AS cnt")[0]["cnt"]
        client.run_cypher("MATCH (n) DETACH DELETE n")
    finally:
        client.close()

    return {"data": {"relationships_deleted": rel_count, "nodes_deleted": node_count}}
