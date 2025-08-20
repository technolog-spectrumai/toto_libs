from django.urls import path
from .views import resume_list, resume_detail

urlpatterns = [
    path('resumes/', resume_list, name='resume_list'),
    path('resume/<int:resume_id>/', resume_detail, name='resume_detail'),
]
