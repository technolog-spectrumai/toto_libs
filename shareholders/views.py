from django.views.generic import ListView, DetailView
from .models import Company, Shareholder
from .page import PageProcessor


class CompanyListView(ListView):
    model = Company
    template_name = 'shareholders/company_list.html'
    context_object_name = 'companies'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        decorated_context = PageProcessor().decorate(context, self.request)
        return decorated_context


class CompanyDetailView(DetailView):
    model = Company
    template_name = 'shareholders/company_detail.html'
    context_object_name = 'company'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        shareholders = Shareholder.objects.filter(company=self.object)
        context['shareholders'] = shareholders
        context['total_shares'] = sum(s.shares_owned for s in shareholders)
        context['share_labels'] = [s.full_name for s in shareholders]
        context['share_data'] = [s.shares_owned for s in shareholders]
        decorated_context = PageProcessor().decorate(context, self.request)
        return decorated_context
