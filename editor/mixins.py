# mixins.py
from .page import PageProcessor

class PageDecoratedMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)
