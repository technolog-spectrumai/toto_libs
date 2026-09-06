"""Company routes, split by which app each one actually needs.

`toto.company` is installable on its own — the descriptive half of the Business
Center. The governance routes below need `toto.ledger` (the share-register
chain), `toto.voting` (meetings and ballots) or `toto.documents` (the PDF
export), and are registered only when those apps are. Registering them
unconditionally would give every governance link a working reverse() and a
500 at the far end; leaving them out means `NoReverseMatch`, which templates
and `_tabs()` already handle by not offering the link.
"""

from django.apps import apps
from django.urls import path

from . import views

app_name = "company"

urlpatterns = [
    path("", views.index, name="index"),
    path("<slug:slug>/", views.structure, name="structure"),
    path("<slug:slug>/shareholders/", views.shareholders, name="shareholders"),
    path("<slug:slug>/chart/", views.org_chart, name="org_chart"),
    path("<slug:slug>/locations/", views.locations, name="locations"),
    # The company's calendar. GET only — events are made in toto.events.
    path("<slug:slug>/events/", views.events, name="events"),
    path("<slug:slug>/graph.json", views.company_graph, name="company_graph"),
    path(
        "<slug:company_slug>/departments/<slug:slug>/",
        views.department_detail,
        name="department_detail",
    ),
    path(
        "<slug:company_slug>/departments/<slug:slug>/edit/",
        views.department_edit,
        name="department_edit",
    ),
    path(
        "<slug:company_slug>/departments/<slug:slug>/members/",
        views.membership_add,
        name="membership_add",
    ),
    path(
        "<slug:company_slug>/departments/<slug:slug>/graph.json",
        views.department_graph,
        name="department_graph",
    ),
]

if apps.is_installed("toto.ledger"):
    urlpatterns += [
        path("<slug:slug>/actions/", views.actions, name="actions"),
        path("<slug:slug>/actions/<uuid:uid>/record/", views.action_record,
             name="action_record"),
        path("<slug:slug>/actions/<uuid:uid>/delete/", views.action_delete,
             name="action_delete"),
    ]

if apps.is_installed("toto.voting"):
    urlpatterns += [
        path("<slug:slug>/votes/", views.votes, name="votes"),
        path("<slug:slug>/votes/<uuid:uid>/", views.vote_detail, name="vote_detail"),
        path("<slug:slug>/votes/<uuid:uid>/open/", views.vote_open, name="vote_open"),
        path("<slug:slug>/votes/<uuid:uid>/<uuid:proposition_uid>/cast/",
             views.vote_cast, name="vote_cast"),
        path("<slug:slug>/votes/<uuid:uid>/<uuid:proposition_uid>/finalize/",
             views.vote_finalize, name="vote_finalize"),
        path("<slug:slug>/votes/<uuid:uid>/export.pdf", views.vote_export,
             name="meeting_export"),
        path("<slug:slug>/votes/<uuid:uid>/<uuid:proposition_uid>/export.pdf",
             views.vote_export, name="vote_export"),
    ]

if apps.is_installed("toto.documents"):
    urlpatterns += [
        path("<slug:slug>/shareholders/register.pdf", views.register_export,
             name="register_export"),
    ]
