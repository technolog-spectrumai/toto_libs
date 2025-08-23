from django.views.generic import ListView, DetailView
from .models import Document, Department, SubSection
from .mixins import PageDecoratedMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.db.models import Prefetch


class DocumentListView(PageDecoratedMixin, ListView):
    model = Document
    template_name = 'document/document_list.html'
    context_object_name = 'documents'

    def get_queryset(self):
        queryset = super().get_queryset()
        department_id = self.request.GET.get('department')
        if department_id:
            queryset = queryset.filter(department_id=department_id)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['departments'] = Department.objects.all()
        context['selected_department'] = self.request.GET.get('department')
        return context


class DocumentDetailView(PageDecoratedMixin, DetailView):
    model = Document
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        document = self.get_object()
        # Prefetch subsections for each section
        sections = document.sections.prefetch_related(
            Prefetch('subsections', queryset=SubSection.objects.order_by('order'))
        )

        context['sections'] = sections
        context['tags'] = document.tags.all()
        return context


def document_pdf_view(request, slug):
    document = get_object_or_404(Document, slug=slug)
    pdf_path = document.generate_pdf()

    try:
        return FileResponse(open(pdf_path, 'rb'), content_type='application/pdf')
    except FileNotFoundError:
        raise Http404("PDF could not be generated.")
