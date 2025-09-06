from django.views.generic import ListView, DetailView
from .models import Document, HtmlDocument, Department, HTMLSubSection
from .mixins import PageDecoratedMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.db.models import Prefetch
from .pdf import generate_document_pdf


class DocumentListView(PageDecoratedMixin, ListView):
    model = HtmlDocument
    template_name = 'documents/document_list.html'
    context_object_name = 'documents'

    def get_queryset(self):
        queryset = super().get_queryset().select_related('project', 'author')
        project_id = self.request.GET.get('project')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['projects'] = Project.objects.all()
        context['selected_project'] = self.request.GET.get('project')
        return context


class DocumentDetailView(PageDecoratedMixin, DetailView):
    model = HtmlDocument
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        document = self.get_object()

        # Prefetch HTML subsections only
        sections = document.sections.prefetch_related(
            Prefetch('html_subsections', queryset=HTMLSubSection.objects.order_by('order'))
        )

        context['sections'] = sections
        context['tags'] = document.project.tags.all()
        context['department'] = document.project.department
        return context


def document_pdf_view(request, slug):
    document = get_object_or_404(Document, slug=slug)
    pdf_path = generate_document_pdf(document)

    try:
        return FileResponse(open(pdf_path, 'rb'), content_type='application/pdf')
    except FileNotFoundError:
        raise Http404("PDF could not be generated.")


