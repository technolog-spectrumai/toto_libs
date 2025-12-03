from django.urls import path
from . import views

app_name = "webfront"

urlpatterns = [
    path("<slug:slug>/", views.page_detail, name="page_detail"),
]
