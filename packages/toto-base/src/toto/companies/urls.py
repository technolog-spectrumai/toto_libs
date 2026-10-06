from django.urls import path

from . import views

app_name = "companies"

# Every door is POST only, for the community's head and administrators, and
# names the company in its address; a holding is looked up within that
# company and answers 404 otherwise (views.py).
urlpatterns = [
    path("<slug:slug>/number/", views.number, name="number"),
    path("<slug:slug>/holdings/", views.holding_save, name="holding_save"),
    path("<slug:slug>/holdings/<int:pk>/delete/", views.holding_delete,
         name="holding_delete"),
]
