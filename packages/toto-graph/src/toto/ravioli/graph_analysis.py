import csv
import io
import json


def load_query_graph(query_id, refresh=False):
    """Return (nodes, edges) from cache; re-runs Neo4j if refresh=True or no cache."""
    from django.utils import timezone

    from .models import CypherQuery, CypherQueryResult

    query = CypherQuery.objects.get(pk=query_id)

    if not refresh:
        result = CypherQueryResult.objects.filter(query=query).first()
        if result and result.result_nodes is not None:
            return result.result_nodes, result.result_edges or []

    from .connection import Neo4jClient, is_enabled

    if not is_enabled():
        raise RuntimeError(
            "RAVIOLI_ENABLED is False — cannot connect to Neo4j. "
            "Run the Cypher query first to populate the cache."
        )

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

    return nodes, edges


def build_networkx_graph(nodes, edges):
    """Build a MultiDiGraph from Ravioli node/edge dicts."""
    try:
        import networkx as nx
    except ImportError as exc:
        raise ImportError(
            "networkx is required for graph analysis. "
            "Install it with: pip install networkx"
        ) from exc

    G = nx.MultiDiGraph()

    for node in nodes:
        props = node.get("props") or {}
        G.add_node(node["id"], labels=node.get("labels", []), **props)

    for edge in edges:
        props = edge.get("props") or {}
        G.add_edge(
            edge["start"],
            edge["end"],
            key=edge.get("id"),
            edge_type=edge.get("type", ""),
            **props,
        )

    return G


def serialize_graph(G):
    """Return JSON-safe node_link_data dict."""
    try:
        import networkx as nx
    except ImportError as exc:
        raise ImportError("networkx is required for graph analysis.") from exc
    return nx.node_link_data(G)


def serialize_output(data, fmt):
    """
    Serialize data to (bytes, content_type, extension).
    fmt: 'json' | 'yaml' | 'csv'
    CSV accepts list[dict] or flattens a plain dict to key/value rows.
    """
    fmt = fmt.lower()

    if fmt == "json":
        payload = json.dumps(data, indent=2, default=str).encode("utf-8")
        return payload, "application/json", ".json"

    if fmt == "yaml":
        try:
            import yaml
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for YAML output. "
                "Install it with: pip install pyyaml"
            ) from exc
        payload = yaml.dump(data, default_flow_style=False, allow_unicode=True).encode("utf-8")
        return payload, "application/x-yaml", ".yaml"

    if fmt == "csv":
        buf = io.StringIO()
        if isinstance(data, list) and data and isinstance(data[0], dict):
            fieldnames = list(data[0].keys())
            writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(data)
        elif isinstance(data, dict):
            writer = csv.writer(buf)
            writer.writerow(["key", "value"])
            for k, v in data.items():
                writer.writerow([k, str(v)])
        else:
            raise ValueError(
                f"Cannot serialize {type(data).__name__} as CSV. "
                "Provide a list[dict] or a dict."
            )
        return buf.getvalue().encode("utf-8"), "text/csv", ".csv"

    raise ValueError(f"Unsupported format: {fmt!r}. Use json, yaml, or csv.")
