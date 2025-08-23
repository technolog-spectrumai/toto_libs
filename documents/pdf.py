from django.template.loader import render_to_string
from django.utils.timezone import now
from weasyprint import HTML
import os
import tempfile
from django.conf import settings


def build_subsection(sub):
    image_path = None
    if sub.image and sub.image.file:
        image_path = os.path.join(settings.MEDIA_ROOT, sub.image.file.name)

    return {
        "order": sub.order,
        "title": sub.title,
        "content": sub.content,
        "created_at": sub.created_at,
        "image": sub.image,
        "image_path": image_path,
    }

def build_section(section):
    return {
        "order": section.order,
        "heading": section.heading,
        "content": section.content,
        "created_at": section.created_at,
        "subsections": [build_subsection(sub) for sub in section.subsections.all()],
    }


def generate_document_pdf(document):
    seal_path = None
    if document.department.seal:
        seal_path = os.path.join(settings.MEDIA_ROOT, document.department.seal.name)

    sections_data = [build_section(section) for section in document.sections.prefetch_related('subsections__image').all()]

    html_string = render_to_string("documents/pdf.html", {
        "document": document,
        "sections": sections_data,
        "seal_path": seal_path,
        "copyright_holder": "SpectrumAi.pl",
        "now": now(),
    })

    base_url = settings.MEDIA_ROOT
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as output:
        HTML(string=html_string, base_url=base_url).write_pdf(output.name)
        return output.name

