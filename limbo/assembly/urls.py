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
    path("communities/<slug:slug>/senate/", views.senate_review, name="senate_review"),
    path("communities/<slug:slug>/senate/nominate/", views.senate_nominate, name="senate_nominate"),
    path("communities/<slug:slug>/senate/appoint/", views.senate_appoint_federal, name="senate_appoint_federal"),
    path("communities/<slug:slug>/senate/<int:proposal_id>/veto/", views.senate_veto, name="senate_veto"),
    path("communities/<slug:slug>/senate/<int:proposal_id>/confirm/", views.senate_confirm, name="senate_confirm"),
]
