"""Two tabs, and one detail shape shared by both.

`<str:kind>` rather than two parallel URL trees: a poll and a vote are the same
object with different rules, and two sets of near-identical routes is how the
two drift apart.
"""

from django.urls import path

from . import quiz_views, views

app_name = "polls"

urlpatterns = [
    path("", views.poll_list, name="poll_list"),
    path("votes/", views.vote_list, name="vote_list"),
    # Fixed routes BEFORE the <kind>/<slug> catch-alls: "votes/new/" would
    # otherwise match as kind="votes", slug="new".
    path("votes/new/", views.vote_create, name="vote_create"),
    path("votes/record-paper/", views.vote_record_paper, name="vote_record_paper"),
    path("ledger/", views.decision_ledger, name="decision_ledger"),
    path("ledger/verify/", views.ledger_verify, name="ledger_verify"),
    path("snapshots/", views.snapshot_list, name="snapshots"),
    path("snapshots/take/", views.snapshot_take, name="snapshot_take"),
    path("snapshots/verify/", views.snapshot_verify, name="snapshot_verify"),
    path("snapshots/<int:pk>/delete/", views.snapshot_delete, name="snapshot_delete"),
    path("ledger/checkpoints/", views.ledger_checkpoints, name="ledger_checkpoints"),
    path("ledger/checkpoints/new/", views.ledger_checkpoint_new, name="ledger_checkpoint_new"),
    path("ledger/checkpoints/verify/", views.ledger_checkpoint_verify, name="ledger_checkpoint_verify"),
    path("electorates/", views.electorate_list, name="electorate_list"),
    path("electorates/<slug:slug>/", views.electorate_detail, name="electorate_detail"),
    path("ledger/export.pdf", views.ledger_pdf_export, name="ledger_pdf"),
    # Quizzes: fixed prefix, so it can never collide with the kind catch-all.
    path("quizzes/", quiz_views.quiz_list, name="quiz_list"),
    path("quizzes/<slug:slug>/", quiz_views.quiz_take, name="quiz_take"),
    path("quizzes/<slug:slug>/statistics/", quiz_views.quiz_statistics, name="quiz_statistics"),
    path("quizzes/<slug:slug>/attempts/<int:number>/", quiz_views.quiz_result, name="quiz_result"),
    path("quizzes/<slug:slug>/attempts/<int:number>/certificate.pdf", quiz_views.quiz_certificate_pdf, name="quiz_certificate"),
    path("<str:kind>/<slug:slug>/", views.question_detail, name="question_detail"),
    path("<str:kind>/<slug:slug>/vote/", views.question_vote, name="question_vote"),
    path("<str:kind>/<slug:slug>/results/", views.question_results, name="question_results"),
    path("<str:kind>/<slug:slug>/close/", views.question_close, name="question_close"),
    path("<str:kind>/<slug:slug>/decision.pdf", views.decision_pdf, name="decision_pdf"),
]
