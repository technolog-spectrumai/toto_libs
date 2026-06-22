from django.urls import path

from . import views

app_name = "ingestor"

urlpatterns = [
    path("", views.home, name="home"),
    path("generate/", views.generate, name="generate"),
    path("proposals/<int:pk>/", views.proposal_detail, name="proposal_detail"),
    path("proposals/<int:pk>/nodes/<str:temp_id>/", views.patch_node, name="patch_node"),
    path("proposals/<int:pk>/rels/<str:temp_id>/", views.patch_rel, name="patch_rel"),
    path("proposals/<int:pk>/apply/", views.apply, name="apply"),
]
