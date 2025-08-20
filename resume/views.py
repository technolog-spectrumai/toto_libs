from django.shortcuts import render, get_object_or_404
from .models import Resume

def resume_detail(request, resume_id):
    resume = get_object_or_404(Resume, id=resume_id)
    context = {
        'resume': resume,
        'work_experiences': resume.work_experiences.all(),
        'education_entries': resume.education_entries.all(),
        'distinctions': resume.distinctions.all(),
        'languages': resume.languages.all(),
        'skills': resume.skills.all(),
    }
    return render(request, 'resume/resume_detail.html', context)

def resume_list(request):
    resumes = Resume.objects.all().order_by('-created_at')
    return render(request, 'resume/resume_list.html', {'resumes': resumes})