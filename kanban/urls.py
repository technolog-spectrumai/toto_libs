from django.urls import path
from .views import (
    ProjectListView,
    ProjectDetailView,
    EisenhowerMatrixView
)

app_name = 'kanban'

urlpatterns = [
    path('', ProjectListView.as_view(), name='project_list'),
    path('project/<int:pk>/', ProjectDetailView.as_view(), name='project_detail'),
    path("project/<int:pk>/matrix/", EisenhowerMatrixView.as_view(), name="eisenhower_matrix"),
]
