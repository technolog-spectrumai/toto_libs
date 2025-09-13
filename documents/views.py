from django.views.generic import DetailView, ListView
from django.db.models import Prefetch
from .models import Document, Department, Section, SubSection
from .mixins import PageDecoratedMixin

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


class DocumentDetailView(PageDecoratedMixin, DetailView):
    model = Document
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        document = self.object
        depth = document.depth

        sections = []
        if depth == 1:
            content = document.content
        elif depth == 2:
            sections = document.sections.all().order_by('order')
            content = None
        elif depth == 3:
            sections = document.sections.prefetch_related(
                Prefetch('subsections', queryset=SubSection.objects.order_by('order'))
            )
            content = None
        else:
            content = None

        context.update({
            'content': content,
            'sections': sections,
            'tags': document.tags.all(),
            'department': document.department,
            'preset': document.preset,
            'depth': depth,
            'engine': document.engine,
        })
        return context
