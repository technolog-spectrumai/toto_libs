from django.views.generic import DetailView, ListView
from django.db.models import Prefetch
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from .models import Department, Document, LatexDocument, HTMLDocument
from .mixins import PageDecoratedMixin
from .pdf import LaTeXToPDFConverter
import os

# ────────────────────────────────────────────────
# 📄 Document List View (All Types)
# ────────────────────────────────────────────────

class DocumentListView(PageDecoratedMixin, ListView):
    model = Document
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
        for doc in context['documents']:
            doc.type_label = doc.document_type
        context.update({
            'departments': Department.objects.all(),
            'selected_department': self.request.GET.get('department')
        })
        return context

# ────────────────────────────────────────────────
# 📄 Document Detail View (Polymorphic)
# ────────────────────────────────────────────────

class DocumentDetailView(PageDecoratedMixin, DetailView):
    model = Document
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        instance = self.object.get_real_instance()

        preview_html = ""
        preview_css = ""

        if isinstance(instance, HTMLDocument):
            preview_html = instance.html
            if instance.preset and instance.preset.css_classes:
                preview_css = " ".join(instance.preset.css_classes.values())

        context.update({
            'preview': preview_html,
            'preview_css': preview_css,
            'department': instance.department,
            'tags': instance.tags.all(),
        })
        return context

# ────────────────────────────────────────────────
# 📄 PDF Export View (LaTeX Only)
# ────────────────────────────────────────────────

def document_pdf_view(request, slug):
    document = get_object_or_404(
        LatexDocument.objects.select_related('department', 'created_by').prefetch_related('tags'),
        slug=slug
    )

    try:
        generator = LaTeXToPDFConverter(document)
        pdf_output = generator.generate_pdf()
        if isinstance(pdf_output, str) and os.path.exists(pdf_output):
            return FileResponse(open(pdf_output, 'rb'), content_type='application/pdf')
        else:
            raise Http404("PDF could not be generated.")

    except Exception as e:
        raise Http404(f"PDF generation failed. {e}")
