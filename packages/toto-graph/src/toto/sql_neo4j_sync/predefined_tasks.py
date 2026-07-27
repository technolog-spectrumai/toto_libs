from toto.workflows.predefined_tasks import register


@register("sql_neo4j_sync_generate_plan")
def sql_neo4j_sync_generate_plan(input_data: dict) -> dict:
    from toto.ravioli.connection import Neo4jClient, is_enabled

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


@register("sql_neo4j_sync_apply_plan")
def sql_neo4j_sync_apply_plan(input_data: dict) -> dict:
    from toto.ravioli.connection import Neo4jClient, is_enabled

    from .models import GraphProjectionPlan
    from .planner import apply_projection_plan

    if not is_enabled():
        raise RuntimeError("RAVIOLI_ENABLED is False — cannot connect to Neo4j.")

    plan_id = (input_data.get("data") or {}).get("plan_id")
    if plan_id is None:
        raise ValueError("sql_neo4j_sync_apply_plan requires plan_id in input data from previous node.")

    plan = GraphProjectionPlan.objects.get(pk=plan_id)
    if plan.status != GraphProjectionPlan.STATUS_READY:
        raise RuntimeError(f"Plan #{plan_id} is not ready (status: {plan.status}).")

    client = Neo4jClient()
    try:
        apply_projection_plan(client, plan)
    finally:
        client.close()

    return {"data": {"plan_id": plan.pk, "status": plan.status, "total_changes": plan.total_changes}}


@register("sql_neo4j_sync_load_neojson")
def sql_neo4j_sync_load_neojson(input_data: dict) -> dict:
    """Load a NeoJSON document into Neo4j via the shared sync engine.

    Input ``data`` keys: ``neojson`` (the document text) or ``vault_file_id``;
    optional ``mode`` ("merge" default, or "replace").
    """
    from toto.ravioli import neojson
    from toto.ravioli.connection import Neo4jClient, is_enabled
    from toto.vault.models import VaultFile

    from . import graphsync

    if not is_enabled():
        raise RuntimeError("RAVIOLI_ENABLED is False — cannot connect to Neo4j.")

    data = input_data.get("data") or {}
    mode = (data.get("mode") or graphsync.MODE_MERGE).strip().lower()
    if mode not in graphsync.MODES:
        raise ValueError(f"Invalid mode {mode!r}; use 'merge' or 'replace'.")

    text = data.get("neojson")
    if text is None and data.get("vault_file_id") is not None:
        vf = VaultFile.objects.get(pk=data["vault_file_id"])
        text = vf.file.read().decode("utf-8")
    if text is None:
        raise ValueError("sql_neo4j_sync_load_neojson requires 'neojson' text or 'vault_file_id'.")

    graph = neojson.loads(text)
    errors = neojson.validate(graph)
    if errors:
        raise ValueError("Invalid NeoJSON: " + "; ".join(errors))

    client = Neo4jClient()
    try:
        summary = graphsync.load_neojson(client, graph, mode=mode)
    finally:
        client.close()

    return {"data": {"summary": summary}}


@register("sql_neo4j_sync_clear_db")
def sql_neo4j_sync_clear_db(input_data: dict) -> dict:
    from toto.ravioli.connection import Neo4jClient, is_enabled

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
