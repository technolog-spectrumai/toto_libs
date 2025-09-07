from celery import shared_task
from .models import HtmlDocument, LatexDocument
from .convert import HTMLToLaTeXConverter, LaTeXToHTMLConverter

@shared_task
def convert_html_to_latex_task(document_id):
    try:
        doc = HtmlDocument.objects.get(pk=document_id)
        for section in doc.sections.all():
            HTMLToLaTeXConverter(section).convert()

        LatexDocument.objects.create(
            project=doc.project,
            author=doc.author,
            status=doc.status,
        ).sections.set(doc.sections.all())

        doc.delete()
    except Exception as e:
        return f"Conversion failed: {str(e)}"
    return "Conversion successful"

@shared_task
def convert_latex_to_html_task(document_id):
    try:
        doc = LatexDocument.objects.get(pk=document_id)
        for section in doc.sections.all():
            LaTeXToHTMLConverter(section).convert()

        HtmlDocument.objects.create(
            project=doc.project,
            author=doc.author,
            status=doc.status,
            summary=""
        ).sections.set(doc.sections.all())

        doc.delete()
    except Exception as e:
        return f"Conversion failed: {str(e)}"
    return "Conversion successful"