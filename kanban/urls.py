from django.urls import path
from .views import (
    ProjectListView,
    ProjectDetailView,
    EisenhowerMatrixView,
    BacklogView,
    MetricsView,   # 👈 import the metrics view
)

app_name = 'kanban'

urlpatterns = [
    path('', ProjectListView.as_view(), name='project_list'),
    path('project/<int:pk>/', ProjectDetailView.as_view(), name='project_detail'),
    path("project/<int:pk>/matrix/", EisenhowerMatrixView.as_view(), name="eisenhower_matrix"),
    path("project/<int:pk>/backlog/", BacklogView.as_view(), name="backlog"),
    path("project/<int:pk>/metrics/", MetricsView.as_view(), name="metrics"),  # 👈 new route
]
