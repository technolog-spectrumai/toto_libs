from celery import shared_task
from .models import HtmlDocument, LatexDocument
from .convert import HTMLToLaTeXConverter, LaTeXToHTMLConverter

@shared_task
def convert_html_to_latex_task(document_id):
    try:
        doc = HtmlDocument.objects.get(pk=document_id)
        for section in doc.sections.all():
            HTMLToLaTeXConverter(section).convert()

        new_doc = LatexDocument.objects.create(
            author=doc.author,
            status=doc.status,
            department=doc.department,
            title=doc.title
        )
        new_doc.tags.set(doc.tags.all())
        new_doc.sections.set(doc.sections.all())
        #doc.delete()
    except Exception as e:
        return f"Conversion failed: {str(e)}"
    return "Conversion successful"

@shared_task
def convert_latex_to_html_task(document_id):
    try:
        doc = LatexDocument.objects.get(pk=document_id)
        for section in doc.sections.all():
            LaTeXToHTMLConverter(section).convert()

        new_doc = HtmlDocument.objects.create(
            author=doc.author,
            status=doc.status,
            department=doc.department,
            title=doc.title,
            summary=""
        )
        new_doc.tags.set(doc.tags.all())
        new_doc.sections.set(doc.sections.all())
        #doc.delete()
    except Exception as e:
        return f"Conversion failed: {str(e)}"
    return "Conversion successful"