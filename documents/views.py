from django.views.generic import DetailView, ListView
from django.db.models import Prefetch
from .models import Department, HTMLDocument, HTMLSection, HTMLSubSection
from .mixins import PageDecoratedMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from .models import HTMLDocument
from .pdf import LaTeXToPDFConverter
import os


# ────────────────────────────────────────────────
# 📄 HTML Document List View
# ────────────────────────────────────────────────

class DocumentListView(PageDecoratedMixin, ListView):
    model = HTMLDocument
    template_name = 'documents/document_list.html'
    context_object_name = 'documents'

    def get_queryset(self):
        queryset = super().get_queryset().select_related('department', 'created_by')
        department_id = self.request.GET.get('department')
        if department_id:
            queryset = queryset.filter(department_id=department_id)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'departments': Department.objects.all(),
            'selected_department': self.request.GET.get('department')
        })
        return context

# ────────────────────────────────────────────────
# 📄 HTML Document Detail View
# ────────────────────────────────────────────────

class DocumentDetailView(PageDecoratedMixin, DetailView):
    model = HTMLDocument
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        document = self.object

        if document.deep:
            sections = document.sections.prefetch_related(
                Prefetch('subsections', queryset=HTMLSubSection.objects.order_by('order'))
            ).order_by('order')
        else:
            sections = []

        context.update({
            'sections': sections,
            'tags': document.tags.all(),
            'department': document.department,
        })
        return context


def document_pdf_view(request, slug):
    document = get_object_or_404(
        HTMLDocument.objects.select_related('department', 'created_by', 'linked_latex').prefetch_related('tags'),
        slug=slug
    )

    latex_doc = document.linked_latex
    if not latex_doc:
        raise Http404("No linked LaTeX document found for PDF generation.")

    try:
        generator = LaTeXToPDFConverter(latex_doc)
        pdf_output = generator.generate_pdf()
        if isinstance(pdf_output, str) and os.path.exists(pdf_output):
            return FileResponse(open(pdf_output, 'rb'), content_type='application/pdf')
        else:
            raise Http404("PDF could not be generated.")

    except Exception as e:
        raise Http404(f"PDF generation failed. {e}")
