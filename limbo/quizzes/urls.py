from django.urls import path

from . import views

app_name = "quizzes"

urlpatterns = [
    path("", views.QuizListView.as_view(), name="quiz-list"),
    path("<int:pk>/", views.quiz_detail, name="quiz-detail"),
    path("<int:pk>/metrics/", views.quiz_metrics, name="quiz-metrics"),
]
