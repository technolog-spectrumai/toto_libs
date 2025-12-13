from django.shortcuts import render, get_object_or_404
from .models import StaticPage, DynamicPage
from oya.page import PageProcessor
from django.template import Template, Context


def static_page_detail(request, slug):
    """
    Display a single StaticPage by slug, decorated with PageProcessor.
    """
    page = get_object_or_404(StaticPage, slug=slug)

    # Normalize into the same structure: page.body is already HTML
    context = {
        "page": page,
    }

    decorated_context = PageProcessor().decorate(context, request)
    return render(request, "webfront/page.html", decorated_context)


def dynamic_page_detail(request, slug):
    """
    Display a DynamicPage by slug, rendering its JSON data into the linked HtmlTemplate.
    """
    page = get_object_or_404(DynamicPage, slug=slug)

    # Base context includes both JSON data and request
    context = {
        "data": page.data,
        "request": request,
    }

    # If a LambdaNode is attached, execute it to transform/enrich context
    if page.lambda_node:
        result = page.lambda_node.execute(context)
        # Merge result back into context
        if isinstance(result, dict):
            context.update(result)

    # Render JSON data into the template content → becomes page.body
    preamble = "{% load include_from_db %}\n"
    template = Template(preamble + page.template.content)
    rendered_body = template.render(Context(context))

    # Mutate page-like object for consistency
    page.body = rendered_body

    decorated_context = PageProcessor().decorate({"page": page}, request)
    return render(request, "webfront/page.html", decorated_context)
