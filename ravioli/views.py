from django.shortcuts import render
from oya.page import PageProcessor
from django.contrib.auth.decorators import login_required
import networkx as nx
from django.http import JsonResponse
from neomodel import db


def get_query(node_types, hops=1, offset=0, limit=50):
    """
    Build a Cypher query string for the given node types.
    Supports single or multiple labels.
    """
    # Normalize node types list
    node_types = [nt.strip() for nt in node_types if nt.strip()]

    if len(node_types) == 1:
        # Single label → MATCH (n:Label)
        label_clause = f":{node_types[0]}"
        query = f"""
            MATCH (n{label_clause})-[r*1..{hops}]-(m)
            RETURN n, r, m
            SKIP {offset} LIMIT {limit}
        """
    else:
        # Multiple labels → use WHERE with labels(n)
        label_list = ",".join([f"'{nt}'" for nt in node_types])
        query = f"""
            MATCH (n)-[r*1..{hops}]-(m)
            WHERE any(label IN labels(n) WHERE label IN [{label_list}])
            RETURN n, r, m
            SKIP {offset} LIMIT {limit}
        """
    return query


@login_required
def graph_chunk(request):
    offset = int(request.GET.get("offset", 0))
    limit = int(request.GET.get("limit", 50))
    hops = 1

    node_type_param = request.GET.get("node_type", "Note")
    node_types = node_type_param.split(",")

    query = get_query(node_types, hops=hops, offset=offset, limit=limit)
    results, meta = db.cypher_query(query)

    # Build a temporary NetworkX graph
    G = nx.Graph()
    seen = set()
    edges = []

    for n, r, m in results:
        if n.id not in seen:
            node_data = dict(n._properties)  # all properties
            node_data.update({
                "id": n.id,
                "type": list(n.labels)[0]
            })
            G.add_node(n.id, **node_data)
            seen.add(n.id)
        if m.id not in seen:
            node_data = dict(m._properties)
            node_data.update({
                "id": m.id,
                "type": list(m.labels)[0]
            })
            G.add_node(m.id, **node_data)
            seen.add(m.id)

        # r is a list of relationships when using variable-length paths
        if isinstance(r, list):
            for rel in r:
                G.add_edge(n.id, m.id, id=str(rel.id), label=rel.type)
                edges.append({"id": str(rel.id), "source": n.id, "target": m.id, "label": rel.type})
        else:
            G.add_edge(n.id, m.id, id=str(r.id), label=r.type)
            edges.append({"id": str(r.id), "source": n.id, "target": m.id, "label": r.type})

    # Compute force-directed layout (spring layout)
    pos = nx.spring_layout(G, k=0.25, iterations=50)

    # Build nodes list with positions
    nodes = []
    for node_id, attrs in G.nodes(data=True):
        x, y = pos[node_id]
        node_entry = {
            "id": node_id,
            "x": float(x),
            "y": float(y),
            "size": 10,
            "attrs": attrs
        }
        nodes.append(node_entry)

    return JsonResponse({"nodes": nodes, "edges": edges})


@login_required
def graph_view(request):
    # Get all node types (labels) from Neo4j
    results, _ = db.cypher_query("CALL db.labels()")
    node_types = [record[0] for record in results]

    context = {
        "title": "Knowledge Graph Explorer",
        "node_types": node_types,
        "selected_node_type": request.GET.get("node_type", ""),
    }
    return render(request, "ravioli/graph.html", PageProcessor().decorate(context, request))