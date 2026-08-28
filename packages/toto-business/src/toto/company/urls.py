from django.urls import path

from . import views

app_name = "company"

urlpatterns = [
    path("", views.index, name="index"),
    path("<slug:slug>/", views.structure, name="structure"),
    path("<slug:slug>/shareholders/", views.shareholders, name="shareholders"),
    path("<slug:slug>/chart/", views.org_chart, name="org_chart"),
    path("<slug:slug>/actions/", views.actions, name="actions"),
    path("<slug:slug>/actions/<uuid:uid>/record/", views.action_record, name="action_record"),
    path("<slug:slug>/actions/<uuid:uid>/delete/", views.action_delete, name="action_delete"),
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
    path("<slug:slug>/locations/", views.locations, name="locations"),
    path("<slug:slug>/shareholders/register.pdf", views.register_export,
         name="register_export"),
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
