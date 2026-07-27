from django.urls import path

from . import views

app_name = "ingestor"

urlpatterns = [
    path("", views.home, name="home"),
    path("generate/", views.generate, name="generate"),
    path("strategies/", views.list_strategies, name="strategies"),
    path("proposals/<int:pk>/", views.proposal_detail, name="proposal_detail"),
    path("proposals/<int:pk>/nodes/<str:temp_id>/", views.patch_node, name="patch_node"),
    path("proposals/<int:pk>/rels/<str:temp_id>/", views.patch_rel, name="patch_rel"),
    path("proposals/<int:pk>/approval/", views.set_approval, name="set_approval"),
    path("proposals/<int:pk>/apply/", views.apply, name="apply"),
]
