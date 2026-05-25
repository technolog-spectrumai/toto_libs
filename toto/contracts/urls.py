from django.urls import path

from . import views

app_name = "contracts"

urlpatterns = [
    path("", views.contract_list, name="contract_list"),
    path("new/", views.contract_create, name="contract_create"),
    path("<uuid:uuid>/", views.contract_detail, name="contract_detail"),
    path("<uuid:uuid>/edit/", views.contract_update, name="contract_update"),
    path("<uuid:uuid>/graph.json", views.contract_graph_json, name="contract_graph_json"),
    path("<uuid:uuid>/nodes/new/", views.contract_node_create, name="contract_node_create"),
    path("<uuid:uuid>/nodes/<int:pk>/edit/", views.contract_node_update, name="contract_node_update"),
    path("<uuid:uuid>/edges/new/", views.contract_edge_create, name="contract_edge_create"),
    path("<uuid:uuid>/edges/<int:pk>/edit/", views.contract_edge_update, name="contract_edge_update"),
]
