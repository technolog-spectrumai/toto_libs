from django.urls import path

from . import views

app_name = "mint"

urlpatterns = [
    path("", views.index, name="index"),
    path("issue/", views.issue, name="issue"),
    path("<int:pk>/mint/", views.mint_units, name="mint_units"),
    path("<int:pk>/burn/", views.burn_units, name="burn_units"),
]
