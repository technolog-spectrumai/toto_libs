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

    result, _ = CypherQueryResult.objects.update_or_create(
        query=query,
        defaults={
            "result_nodes": nodes,
            "result_edges": edges,
            "last_run_at": timezone.now(),
            "error": "",
        },
    )

    # Metering: cypher query + row count (workflow-driven execution)
    from toto.metering.utils import safe_record_usage as _m
    _m(
        metric_code="ravioli.cypher_query",
        quantity=1,
        unit="query",
        source_type="ravioli.CypherQuery",
        source_id=str(query.pk),
        source_label=query.name,
        subject_type="system",
        subject_id="ravioli",
        idempotency_key=f"ravioli.cypher_query:workflow:{query.pk}:{timezone.now().strftime('%Y%m%dT%H%M%S')}",
        metadata={"node_count": len(nodes), "edge_count": len(edges)},
    )
    _row_count = len(nodes) + len(edges)
    if _row_count:
        _m(
            metric_code="ravioli.cypher_row",
            quantity=_row_count,
            unit="row",
            source_type="ravioli.CypherQuery",
            source_id=str(query.pk),
            source_label=query.name,
            subject_type="system",
            subject_id="ravioli",
            idempotency_key=f"ravioli.cypher_row:workflow:{query.pk}:{timezone.now().strftime('%Y%m%dT%H%M%S')}",
        )

    return {"data": {"query_id": query_id, "node_count": len(nodes), "edge_count": len(edges)}}


@register("ravioli_prepare_graph_analysis")
def ravioli_prepare_graph_analysis(input_data: dict) -> dict:
    from .graph_analysis import build_networkx_graph, load_query_graph, serialize_graph

    data = input_data.get("data") or {}
    query_id = data.get("query_id")
    refresh = bool(data.get("refresh", False))

    if query_id is None:
        raise ValueError("ravioli_prepare_graph_analysis requires query_id in input data.")

    nodes, edges = load_query_graph(query_id, refresh=refresh)
    G = build_networkx_graph(nodes, edges)
    graph_payload = serialize_graph(G)

    summary = {
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "query_id": query_id,
    }

    # Pass through all input fields so downstream nodes (e.g. save) can access them
    out = dict(data)
    out.update({"graph": graph_payload, "graph_summary": summary})

    return {"data": out}


@register("ravioli_save_graph_analysis_output")
def ravioli_save_graph_analysis_output(input_data: dict) -> dict:
    import re
    from django.contrib.auth.models import User
    from django.core.files.base import ContentFile
    from django.utils import timezone

    from toto.vault.models import Bucket, VaultDirectory, VaultFile
    from .graph_analysis import serialize_output

    data = input_data.get("data") or {}
    bucket_id = data.get("bucket_id")
    directory_id = data.get("directory_id")
    owner_id = data.get("owner_id")
    fmt = (data.get("format") or "json").lower()
    title = data.get("title") or "Graph Analysis"
    query_id = data.get("query_id")

    if not bucket_id:
        raise ValueError("ravioli_save_graph_analysis_output requires bucket_id.")
    if not owner_id:
        raise ValueError("ravioli_save_graph_analysis_output requires owner_id.")
    if fmt not in ("json", "yaml", "csv"):
        raise ValueError(f"Unsupported format: {fmt!r}. Use json, yaml, or csv.")

    try:
        bucket = Bucket.objects.get(pk=bucket_id)
    except Bucket.DoesNotExist:
        raise ValueError(f"Bucket #{bucket_id} does not exist.")

    directory = None
    if directory_id:
        try:
            directory = VaultDirectory.objects.get(pk=directory_id, bucket=bucket)
        except VaultDirectory.DoesNotExist:
            raise ValueError(
                f"Directory #{directory_id} does not exist in bucket #{bucket_id}."
            )

    try:
        owner = User.objects.get(pk=owner_id)
    except User.DoesNotExist:
        raise ValueError(f"User #{owner_id} does not exist.")

    # Strip plumbing and the raw graph blob; serialize what's left as the result
    _skip = {"bucket_id", "directory_id", "owner_id", "format", "title", "graph"}
    payload = {k: v for k, v in data.items() if k not in _skip}

    content, mime_type, ext = serialize_output(payload, fmt)
    file_type = VaultFile.detect_type(mime_type)

    timestamp = re.sub(r"[^0-9]", "", timezone.now().isoformat()[:19])
    filename = f"graph_analysis_q{query_id or 'x'}_{timestamp}{ext}"

    vault_file = VaultFile(
        owner=owner,
        title=title,
        bucket=bucket,
        directory=directory,
        file_type=file_type,
        is_public=False,
    )
    vault_file.file.save(filename, ContentFile(content), save=False)
    vault_file.save()

    return {"data": {"vault_file_id": vault_file.pk, "download_url": vault_file.get_public_url()}}


@register("ravioli_graph_search")
def ravioli_graph_search(input_data: dict) -> dict:
    """Run a graph search (basic / advanced / deep) and return results."""
    from .services.search import (
        SearchUnavailableError,
        advanced_search,
        basic_search,
        deep_search,
    )

    data = input_data.get("data") or {}
    q = str(data.get("q") or "").strip()
    mode = data.get("mode", "basic")
    limit = max(1, min(200, int(data.get("limit", 25))))
    exact = bool(data.get("exact", False))

    if not q:
        raise ValueError("Search query 'q' is required.")

    if mode not in ("basic", "advanced", "deep"):
        mode = "basic"

    try:
        if mode == "deep":
            results = deep_search(q, limit=limit, exact=exact)
        elif mode == "advanced":
            results = advanced_search(q, limit=limit)
        else:
            results = basic_search(q, limit=limit)
    except SearchUnavailableError as exc:
        raise RuntimeError(f"Neo4j unavailable: {exc}") from exc

    return {
        "data": {
            "q": q,
            "mode": mode,
            "limit": limit,
            "exact": exact,
            "result_count": len(results),
            "results": results,
        }
    }


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
