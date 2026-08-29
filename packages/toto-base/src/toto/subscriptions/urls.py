from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("", views.plans, name="plans"),
    path("mine/", views.mine, name="mine"),
    path("subscribe/<slug:code>/", views.subscribe, name="subscribe"),
    path("cancel/", views.cancel, name="cancel"),
    path("discounts/", views.discounts, name="discounts"),
    path("communities/", views.audience, name="audience"),
]
