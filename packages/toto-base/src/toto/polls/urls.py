"""Two tabs, and one detail shape shared by both.

`<str:kind>` rather than two parallel URL trees: a poll and a vote are the same
object with different rules, and two sets of near-identical routes is how the
two drift apart.
"""

from django.urls import path

from . import views

app_name = "polls"

urlpatterns = [
    path("", views.poll_list, name="poll_list"),
    path("votes/", views.vote_list, name="vote_list"),
    path("<str:kind>/<slug:slug>/", views.question_detail, name="question_detail"),
    path("<str:kind>/<slug:slug>/vote/", views.question_vote, name="question_vote"),
    path("<str:kind>/<slug:slug>/results/", views.question_results, name="question_results"),
]
