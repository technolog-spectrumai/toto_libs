from django.views.generic import DetailView, ListView
from django.db.models import Prefetch
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from .models import Document, Department, Section, SubSection
from .mixins import PageDecoratedMixin
from .pdf import HTMLToPDFConverter, LaTeXToPDFConverter
import os


# ────────────────────────────────────────────────
# 📄 Document List View
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
        context.update({
            'departments': Department.objects.all(),
            'selected_department': self.request.GET.get('department')
        })
        return context


# ────────────────────────────────────────────────
# 📄 Document Detail View
# ────────────────────────────────────────────────

class DocumentDetailView(PageDecoratedMixin, DetailView):
    model = Document
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        document = self.object

        if document.deep:
            sections = document.sections.prefetch_related(
                Prefetch('subsections', queryset=SubSection.objects.order_by('order'))
            ).order_by('order')

            structured = []
            for section in sections:
                subsections = list(section.subsections.all().order_by('order')) if section.deep else []
                structured.append({
                    'section': section,
                    'subsections': subsections,
                    'content': None if section.deep else section.content
                })

            content = None
        else:
            structured = []
            content = document.content

        context.update({
            'content': content,
            'sections': structured,
            'tags': document.tags.all(),
            'department': document.department,
            'preset': document.preset,
            'engine': document.engine,
            'deep': document.deep,
        })
        return context


# ────────────────────────────────────────────────
# 📄 PDF Export View (Inline Generator Logic)
# ────────────────────────────────────────────────

def document_pdf_view(request, slug):
    document = get_object_or_404(
        Document.objects.select_related('department', 'created_by').prefetch_related('tags'),
        slug=slug
    )

    try:
        # ─────────────────────────────────────────────────────────────
        # Inline PDF Generator Dispatcher
        # ─────────────────────────────────────────────────────────────
        engine = document.engine
        if engine == 'latex':
            generator = LaTeXToPDFConverter(document)
        elif engine == 'html':
            generator = HTMLToPDFConverter(document)
        else:
            raise ValueError("Unsupported document type")

        pdf_path = generator.generate_pdf()
        # ─────────────────────────────────────────────────────────────

        if not pdf_path or not os.path.exists(pdf_path):
            raise Http404("PDF could not be generated.")
        return FileResponse(open(pdf_path, 'rb'), content_type='application/pdf')

    except Exception:
        raise Http404("PDF generation failed.")
