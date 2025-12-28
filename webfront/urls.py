from django.urls import path
from django.contrib.auth.decorators import login_required
from . import views

app_name = "webfront"


urlpatterns = [
    # Graph Explorer (requires login)
    path("graph/", login_required(views.graph_explorer), name="graph"),
    path("graph/data/", views.graph_data, name="graph_data"),
    path("page/<slug:slug>/", views.DynamicPageView.as_view(), name="dynamic_page"),
    path("gateway/<slug:slug>/upload/", views.gateway_upload, name="gateway_upload"),
    path("gateway/<slug:slug>/", views.GatewayView.as_view(), name="gateway")
]

