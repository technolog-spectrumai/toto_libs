from django.urls import path
from django.contrib.auth.decorators import login_required
from . import views

app_name = "ravioli"


urlpatterns = [
    # Graph Explorer (requires login)
    path("graph/", login_required(views.graph_explorer), name="graph"),
]

