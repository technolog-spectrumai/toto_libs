from django.urls import path
from . import views

app_name = "assembly"

urlpatterns = [
    path("", views.assembly_overview, name="overview"),
    path("communities/<slug:slug>/", views.community_assembly, name="community_assembly"),
    path("communities/<slug:slug>/proposals/new/", views.proposal_create, name="proposal_create"),
    path("communities/<slug:slug>/proposals/<int:proposal_id>/vote/", views.proposal_vote, name="proposal_vote"),
    path("communities/<slug:slug>/proposals/<int:proposal_id>/close/", views.proposal_close, name="proposal_close"),
    path("communities/<slug:slug>/poll-taxes/<int:poll_tax_id>/pay/", views.poll_tax_pay, name="poll_tax_pay"),
]
