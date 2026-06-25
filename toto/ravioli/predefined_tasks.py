from django.utils import timezone

from toto.workflows.predefined_tasks import register


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
    if fmt not in ("json", "yaml", "csv", "neojson"):
        raise ValueError(f"Unsupported format: {fmt!r}. Use json, yaml, csv, or neojson.")

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

    if fmt == "neojson":
        # NeoJSON serializes the graph DATA itself (nodes + relationships), not a
        # summary — so load the query's cached graph and emit a NeoJSON document.
        from . import neojson
        from .graph_analysis import load_query_graph

        if not query_id:
            raise ValueError("neojson output requires query_id in input data.")

        nodes, edges = load_query_graph(query_id)
        graph = neojson.from_ravioli(
            nodes,
            edges,
            metadata={
                "generated_at": timezone.now().isoformat(),
                "source": f"ravioli:cypher_query/{query_id}",
            },
        )
        content = neojson.dumps(graph).encode("utf-8")
        mime_type, ext = neojson.MIME, neojson.EXTENSION
        file_type = "neojson"
    else:
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
    """Run a graph search and return results with mode metadata.

    Supports modes: keyword (default), fulltext, semantic, and legacy aliases
    basic / advanced / deep.
    """
    from .services.search import MODE_KEYWORD, SearchUnavailableError, resolve_mode, run_search

    data = input_data.get("data") or {}
    q = str(data.get("q") or "").strip()
    mode = resolve_mode(data.get("mode", MODE_KEYWORD))
    limit = max(1, min(200, int(data.get("limit", 25))))
    exact = bool(data.get("exact", False))

    if not q:
        raise ValueError("Search query 'q' is required.")

    try:
        sr = run_search(q, mode=mode, limit=limit, exact=exact)
    except SearchUnavailableError as exc:
        raise RuntimeError(f"Neo4j unavailable: {exc}") from exc

    return {
        "data": {
            "q": q,
            "limit": limit,
            "exact": exact,
            "result_count": len(sr["results"]),
            "results": sr["results"],
            "requested_mode": sr["requested_mode"],
            "effective_mode": sr["effective_mode"],
            "fallback_used": sr["fallback_used"],
            "fallback_reason": sr["fallback_reason"],
            "semantic_available": sr["semantic_available"],
        }
    }


GRAPHRAG_DESCRIBE_PROMPT = (
    "Describe this knowledge graph: the main kinds of entities, how they relate, "
    "and any notable patterns or clusters. Be concise and ground every claim in the "
    "retrieved context."
)


@register("ravioli_graphrag_describe")
def ravioli_graphrag_describe(input_data: dict) -> dict:
    """Answer a question about the graph via GraphRAG (Celery task, "Ask AI").

    Builds the selected Sabbia agent's LLM (provider per settings.GRAPHRAG_PROVIDER)
    and runs the read-only neo4j-graphrag pipeline (vector + Text2Cypher) over the
    graph. Raises on any failure — with the real cause — so the WorkflowRun is
    marked FAILED with a useful message.
    """
    from django.apps import apps as django_apps

    from .connection import is_enabled

    data = input_data.get("data") or {}
    question = (data.get("question") or "").strip() or GRAPHRAG_DESCRIBE_PROMPT
    top_k = int(data.get("top_k", 8))
    text2cypher = bool(data.get("text2cypher", True))

    if not is_enabled():
        raise RuntimeError("RAVIOLI_ENABLED is False — cannot run GraphRAG.")
    if not django_apps.is_installed("toto.sabbia"):
        raise RuntimeError("toto.sabbia is not installed — Steven is unavailable.")

    from django.conf import settings

    from toto.sabbia.graphrag_llm import build_llm
    from toto.sabbia.models import Agent

    # Single deploy switch (settings.GRAPHRAG_PROVIDER) selects the provider; pick
    # the active Sabbia agent whose endpoint_type matches it (openai / ollama),
    # falling back to any active agent.
    provider = (getattr(settings, "GRAPHRAG_PROVIDER", "openai") or "openai").strip().lower()
    endpoint_type = Agent.ENDPOINT_OLLAMA if provider == "ollama" else Agent.ENDPOINT_OPENAI
    agent = (
        Agent.objects.filter(endpoint_type=endpoint_type, is_active=True).order_by("name").first()
        or Agent.objects.filter(is_active=True).order_by("name").first()
    )
    if agent is None:
        raise RuntimeError(
            f"No active Sabbia agent for GRAPHRAG_PROVIDER='{provider}' "
            f"(endpoint_type={endpoint_type}) and no active fallback agent."
        )
    llm = build_llm(agent)
    if llm is None:
        raise RuntimeError(
            f"Could not build the LLM for agent '{agent.name}' (missing API key / connector)."
        )

    from toto.ravioli.rag import run_graphrag

    result = run_graphrag(
        llm=llm, query_text=question, top_k=top_k, text2cypher=text2cypher, detail=True
    )
    answer = result["answer"]
    if not answer:
        raise RuntimeError(
            f"GraphRAG produced no answer (provider={provider}, agent={agent.name}): "
            f"{result['cause'] or 'unknown cause'}"
        )
    return {"data": {
        "question": question, "answer": answer, "context": result["context"],
        "graph": result["graph"], "agent": agent.name,
    }}
