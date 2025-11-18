from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404
from .models import Note
from oya.page import PageProcessor
from django.contrib.auth.decorators import login_required
from community.models import Community, CommunityMember
from portfolio.models import Company
from  ravioli.models import Note
import networkx as nx
from django.http import JsonResponse
from neomodel import db


@login_required
def public_notes_list(request):
    """
    Public notes list with optional category filter and pagination.
    """
    selected_category = request.GET.get("category", "")
    notes_qs = Note.objects.filter(is_public=True).order_by("-created_at")

    if selected_category:
        notes_qs = notes_qs.filter(category=selected_category)

    paginator = Paginator(notes_qs, 10)  # 10 notes per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    categories = (
        Note.objects.filter(is_public=True)
        .exclude(category__isnull=True)
        .exclude(category__exact="")
        .values_list("category", flat=True)
        .distinct()
    )

    context = {
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "categories": categories,
        "selected_category": selected_category,
    }
    return render(
        request,
        "ravioli/public_notes_list.html",
        PageProcessor().decorate(context, request)
    )


def build_subject_data(subject):
    """
    Inspect the subject (SocialEntity subclass) and return a dict
    with type, name, and a unified display_name.
    """
    if subject is None:
        return None

    if isinstance(subject, Company):
        return {
            "type": "company",
            "name": subject.name,
            "display_name": subject.name,  # nice name
        }
    elif isinstance(subject, Community):
        return {
            "type": "community",
            "name": subject.name,
            "display_name": subject.name,  # nice name
        }
    elif isinstance(subject, CommunityMember):
        return {
            "type": "community_member",
            "name": subject.display_name,  # raw display_name
            "display_name": subject.display_name,  # nice name
        }
    else:
        # fallback for generic SocialEntity
        raw_name = getattr(subject, "name", None) or str(subject)
        return {
            "type": "social_entity",
            "name": raw_name,
            "display_name": raw_name,  # nice name
        }



@login_required
def public_note_detail(request, pk):
    note = get_object_or_404(
        Note.objects.select_related("author", "subject", "event").prefetch_related("tags"),
        pk=pk,
        is_public=True,
    )

    subject_data = build_subject_data(note.subject) if note.subject else None
    event_data = None
    if note.event:
        event_data = {
            "title": note.event.title,
            "description": note.event.description,
            "start_time": note.event.start_time,
            "end_time": note.event.end_time,
            "location": note.event.location,
            "organizer": note.event.organizer.username if note.event.organizer else None,
            "company": {
                "name": note.event.company.name,
                "industry": note.event.company.industry,
                "country": note.event.company.country,
                "date_founded": note.event.company.date_founded,
                "is_active": note.event.company.is_active,
            } if note.event.company else None,
            "category": str(note.event.category) if note.event.category else None,
        }

    context = {
        "note": note,
        "note_data": {
            "id": note.id,
            "title": note.title,
            "content": note.content,
            "author": note.author.username if note.author else None,
            "tags": [tag.name for tag in note.tags.all()],
            "category": note.category,
            "created_at": note.created_at,
            "metadata": note.metadata or {},
        },
        "subject_data": subject_data,
        "event_data": event_data,
    }
    return render(
        request,
        "ravioli/public_note_detail.html",
        PageProcessor().decorate(context, request)
    )


@login_required
def graph_chunk(request):
    # Get offset & limit from query params
    offset = int(request.GET.get("offset", 0))
    limit = int(request.GET.get("limit", 50))

    # New args: hops and node_type
    hops = 1
    node_type = request.GET.get("node_type", "Note")  # default: Note

    # Cypher query: start from given node type, expand up to n hops
    query = f"""
        MATCH (n:{node_type})-[r*1..{hops}]-(m)
        RETURN n, r, m
        SKIP {offset} LIMIT {limit}
    """
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