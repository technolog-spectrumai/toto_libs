from django.urls import path
from . import views

app_name = "senate"

urlpatterns = [
    path("", views.senate_overview, name="overview"),
    path("<slug:slug>/", views.community_senate, name="community_senate"),
]
