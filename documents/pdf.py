from django.template.loader import render_to_string
from django.utils.timezone import now
from weasyprint import HTML
import os
import tempfile
from django.conf import settings
from django.db.models import Prefetch
from .models import HTMLSubSection, LaTeXSubSection


def build_subsection(sub):
    image_path = None
    if sub.image and sub.image.file:
        image_path = os.path.join(settings.MEDIA_ROOT, sub.image.file.name)

    return {
        "order": sub.order,
        "title": sub.title,
        "created_at": sub.created_at,
        "image": sub.image,
        "image_path": image_path,
    }


def build_section(section):
    return {
        "order": section.order,
        "heading": section.heading,
        #"subsections": [build_subsection(sub) for sub in section.all_subsections]
    }


def generate_document_pdf(document):
    seal_path = None
    if document.department.seal:
        seal_path = os.path.join(settings.MEDIA_ROOT, document.department.seal.name)

    sections = document.sections.prefetch_related(
        Prefetch('html_subsections', queryset=HTMLSubSection.objects.select_related('image')),
        Prefetch('latex_subsections')
    ).all()

    sections_data = [build_section(section) for section in sections]

    notice = '''This document is confidential and intended 
                solely for the use of the individual or entity to whom it is addressed.
                Unauthorized distribution, reproduction, or disclosure is strictly prohibited.'''

    html_string = render_to_string("documents/pdf.html", {
        "document": document,
        "sections": sections_data,
        "seal_path": seal_path,
        "copyright_holder": "SpectrumAi.pl",
        "copyright_notice": notice,
        "now": now(),
    })

    base_url = settings.MEDIA_ROOT
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as output:
        HTML(string=html_string, base_url=base_url).write_pdf(output.name)
        return output.name

