from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.template.loader import render_to_string
#from weasyprint import HTML
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


def download_resume_pdf(request, resume_id):
    resume = Resume.objects.get(id=resume_id)
    context = {
        'resume': resume,
        'work_experiences': resume.work_experiences.all(),
        'education_entries': resume.education_entries.all(),
        'distinctions': resume.distinctions.all(),
        'languages': resume.languages.all(),
        'skills': resume.skills.all()
    }

    html_string = render_to_string('resume/export_pdf.html', context)
    pdf_file = HTML(string=html_string).write_pdf()

    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{resume.full_name}_resume.pdf"'
    return response
