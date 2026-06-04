from django.urls import path

from toto.tribunal import views

app_name = "tribunal"

urlpatterns = [
    path("", views.tribunal_dashboard, name="dashboard"),

    path("cases/", views.case_list, name="case_list"),
    path("cases/new/", views.case_create, name="case_create"),
    path("orders/<str:order_number>/open-dispute/", views.open_order_dispute, name="open_order_dispute"),
    path("cases/<int:case_id>/", views.case_detail, name="case_detail"),
    path("cases/<int:case_id>/edit/", views.case_update, name="case_update"),
    path("cases/<int:case_id>/resolve/", views.case_resolve, name="case_resolve"),
    path("cases/<int:case_id>/reopen/", views.case_reopen, name="case_reopen"),

    path("cases/<int:case_id>/parties/add/", views.party_create, name="party_create"),
    path("parties/<int:party_id>/edit/", views.party_update, name="party_update"),
    path("parties/<int:party_id>/delete/", views.party_delete, name="party_delete"),

    path("cases/<int:case_id>/claims/add/", views.claim_create, name="claim_create"),
    path("claims/<int:claim_id>/edit/", views.claim_update, name="claim_update"),
    path("claims/<int:claim_id>/delete/", views.claim_delete, name="claim_delete"),

    path("cases/<int:case_id>/evidence/add/", views.evidence_create, name="evidence_create"),
    path("evidence/<int:evidence_id>/edit/", views.evidence_update, name="evidence_update"),
    path("evidence/<int:evidence_id>/delete/", views.evidence_delete, name="evidence_delete"),

    path("cases/<int:case_id>/rulings/add/", views.ruling_create, name="ruling_create"),
    path("rulings/<int:ruling_id>/edit/", views.ruling_update, name="ruling_update"),
    path("rulings/<int:ruling_id>/delete/", views.ruling_delete, name="ruling_delete"),

    path("cases/<int:case_id>/jury/open/", views.jury_session_open, name="jury_session_open"),
    path("jury/<int:session_id>/", views.jury_session_detail, name="jury_session_detail"),
    path("jury/<int:session_id>/vote/", views.jury_vote, name="jury_vote"),
    path("jury/<int:session_id>/close/", views.jury_session_close, name="jury_session_close"),
]
