"""One shape: a list, a question, an answer, a result.

``<str:kind>`` is kept in the path even though there is only one kind now.
Removing it would change every existing URL to a consultation somebody has
already linked to or bookmarked, which is a real cost for a cosmetic gain — and
the day a second kind of consultation appears, the route is already shaped for
it. What is NOT coming back is the formal vote: the ~17 governance routes that
stood here (the ledger, its PDF, checkpoints, snapshots, electorates, the paper
recorder, the vote creator) went to Irena in 1.50.
"""

from django.urls import path

from . import quiz_views, views

app_name = "polls"

urlpatterns = [
    path("", views.poll_list, name="poll_list"),
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
]
