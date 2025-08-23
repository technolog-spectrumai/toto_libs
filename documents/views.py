from django.views.generic import ListView, DetailView
from .models import Document, Office
from .mixins import PageDecoratedMixin


class DocumentListView(PageDecoratedMixin, ListView):
    model = Document
    template_name = 'document/document_list.html'
    context_object_name = 'documents'

    def get_queryset(self):
        queryset = super().get_queryset()
        office_id = self.request.GET.get('office')
        if office_id:
            queryset = queryset.filter(office_id=office_id)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['offices'] = Office.objects.all()
        context['selected_office'] = self.request.GET.get('office')
        return context



class DocumentDetailView(PageDecoratedMixin, DetailView):
    model = Document
    template_name = 'documents/document_detail.html'
    context_object_name = 'document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        document = self.get_object()
        context['sections'] = document.sections.all()
        context['tags'] = document.tags.all()
        return context
