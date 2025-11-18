from django.urls import path
from . import views

app_name = "ravioli"

urlpatterns = [
    path("", views.graph_view, name="graph"),
    path("graph-chunk/", views.graph_chunk, name="graph_chunk"),
]
