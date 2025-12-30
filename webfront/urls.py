from django.urls import path
from django.contrib.auth.decorators import login_required
from . import views

app_name = "webfront"

urlpatterns = [
    path("graph/", login_required(views.graph_explorer), name="graph"),
    path("graph/data/", views.graph_data, name="graph_data"),

    path("page/<slug:slug>/", views.DynamicPageView.as_view(), name="dynamic_page"),

    # NEW: workflow upload endpoint
    path("workflow/<slug:slug>/upload/", views.workflow_upload, name="workflow_upload"),
]
