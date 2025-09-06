from django.views.generic import ListView, DetailView
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.db.models import Prefetch

from .models import Document, Department, Section, HTMLSubSection, LaTeXSubSection
from .mixins import PageDecoratedMixin
from .pdf import generate_document_pdf


class DocumentListView(PageDecoratedMixin, ListView):
    model = Document
    template_name = 'document/document_list.html'
    context_object_name = 'documents'

    def get_queryset(self):
        queryset = super().get_queryset().select_related('department', 'author')
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


class DocumentDetailView(PageDecoratedMixin, DetailView):
    model = Document
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        document = self.object

        # Prefetch both HTML and LaTeX subsections for each section
        sections = document.sections.prefetch_related(
            Prefetch('html_subsections', queryset=HTMLSubSection.objects.order_by('order')),
            Prefetch('latex_subsections', queryset=LaTeXSubSection.objects.order_by('order'))
        )

        context.update({
            'sections': sections,
            'tags': document.tags.all(),
            'department': document.department
        })
        return context


def document_pdf_view(request, slug):
    document = get_object_or_404(
        Document.objects.select_related('department', 'author').prefetch_related('tags'),
        slug=slug
    )

    sections = document.sections.prefetch_related(
        Prefetch('html_subsections'),
        Prefetch('latex_subsections')
    )

    document.sections_prefetched = sections

    try:
        pdf_path = generate_document_pdf(document)
        if not pdf_path:
            raise Http404("PDF could not be generated.")
        # with open(pdf_path, 'rb') as pdf_file:
        #     return FileResponse(pdf_file, content_type='application/pdf')
        pdf_file = open(pdf_path, 'rb')  # ✅ Keep file open
        return FileResponse(pdf_file, content_type='application/pdf')
    except FileNotFoundError:
        raise Http404("PDF could not be generated.")
