from django.urls import path
from . import views

app_name = "notes"

urlpatterns = [
    path("", views.public_notes_list, name="public_notes_list"),
    path("notes/<uuid:pk>/", views.public_note_detail, name="public_note_detail"),
    path("graph/", views.graph_view, name="graph"),
    path("graph-chunk/", views.graph_chunk, name="graph_chunk"),
]
