from django.urls import path
from .views import resume_list, resume_detail, download_resume_pdf

urlpatterns = [
    path('resumes/', resume_list, name='resume_list'),
    path('resume/<int:resume_id>/', resume_detail, name='resume_detail'),
    path('resume/<int:resume_id>/download/', download_resume_pdf, name='download_resume_pdf'),
]
