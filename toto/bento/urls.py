from django.urls import path

from . import views
from .api_views import (
    CategoryListApiView,
    EdgeDetailApiView,
    EdgeListApiView,
    EdgeTypeListApiView,
    FullGraphApiView,
    NodeDetailApiView,
    NodeGraphApiView,
    NodeListApiView,
)

app_name = "bento"

urlpatterns = [
    # nodes
    path("", views.node_list, name="node_list"),
    path("nodes/new/", views.node_create, name="node_create"),
    path("nodes/batch-delete/", views.node_batch_delete, name="node_batch_delete"),
    path("nodes/<str:uid>/", views.node_detail, name="node_detail"),
    path("nodes/<str:uid>/edit/", views.node_update, name="node_update"),
    path("nodes/<str:uid>/delete/", views.node_delete, name="node_delete"),

    # edges
    path("relations/", views.edge_list, name="edge_list"),
    path("edges/new/", views.edge_create, name="edge_create"),
    path("edges/batch-delete/", views.edge_batch_delete, name="edge_batch_delete"),
    path("edges/<path:edge_id>/edit/", views.edge_update, name="edge_update"),
    path("edges/<path:edge_id>/delete/", views.edge_delete, name="edge_delete"),

    # category templates
    path("categories/", views.category_list, name="category_list"),
    path("categories/new/", views.category_create, name="category_create"),
    path("categories/<int:pk>/edit/", views.category_update, name="category_update"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),

    # edge-type templates
    path("edge-types/", views.edgetype_list, name="edgetype_list"),
    path("edge-types/new/", views.edgetype_create, name="edgetype_create"),
    path("edge-types/<int:pk>/edit/", views.edgetype_update, name="edgetype_update"),
    path("edge-types/<int:pk>/delete/", views.edgetype_delete, name="edgetype_delete"),

    # graph (lazy) JSON
    path("api/graph/", views.api_full_graph, name="api_full_graph"),
    path("api/nodes/<str:uid>/graph/", views.api_node_graph, name="api_node_graph"),

    # JSON / CORS API
    path("api/nodes/", NodeListApiView.as_view(), name="api_node_list"),
    path("api/nodes/<str:uid>/", NodeDetailApiView.as_view(), name="api_node_detail"),
    path("api/edges/", EdgeListApiView.as_view(), name="api_edge_list"),
    path("api/edges/<path:edge_id>/", EdgeDetailApiView.as_view(), name="api_edge_detail"),
    path("api/categories/", CategoryListApiView.as_view(), name="api_category_list"),
    path("api/edge-types/", EdgeTypeListApiView.as_view(), name="api_edge_type_list"),
    path("api/full-graph/", FullGraphApiView.as_view(), name="api_full_graph_cors"),
    path("api/node-graph/<str:uid>/", NodeGraphApiView.as_view(), name="api_node_graph_cors"),
]
