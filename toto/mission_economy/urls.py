from django.urls import path

from .views import mission_economy, project_tokenize, project_tokenization_default

app_name = "mission_economy"

urlpatterns = [
    path(
        "mission/<int:pk>/",
        mission_economy,
        name="mission_economy",
    ),
    path(
        "project/<int:pk>/tokenize/",
        project_tokenize,
        name="project_tokenize",
    ),
    path(
        "project/tokenization/<int:pk>/default/",
        project_tokenization_default,
        name="project_tokenization_default",
    ),
]
