from django.views import View
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse

from oya.page import PageProcessor
from webfront.models import DynamicPage

import json


# ---------------------------------------------------------
# Helper: format an error widget
# ---------------------------------------------------------

def make_error_widget(page, title, error):
    return {
        "id": f"error_{page.id}",
        "title": title,
        "error": str(error),
        "data": None,
    }


# ---------------------------------------------------------
# DynamicPage View
# ---------------------------------------------------------

class DynamicPageView(View):
    template_name = "webfront/page.html"

    def get(self, request, slug):
        page = get_object_or_404(DynamicPage, slug=slug)
        processor = PageProcessor()

        result = []
        lambda_result = []

        # -----------------------------------------------------
        # Execute lambda if present
        # -----------------------------------------------------
        if page.lambda_node:
            try:
                lambda_result = page.lambda_node.execute({"request": request}) or []
            except Exception as e:
                result.append(make_error_widget(page, "Lambda Error", e))
                lambda_result = []

        # -----------------------------------------------------
        # Validate lambda output
        # -----------------------------------------------------
        if not isinstance(lambda_result, list):
            result.append(make_error_widget(page, "Invalid Lambda Output",
                                            "Page lambda must return a list of widgets"))
        else:
            for item in lambda_result:
                # If lambda returned an error widget, pass it through
                if "error" in item:
                    result.append(item)
                    continue

                # Normal widget
                result.append({
                    "data": json.dumps(item),
                    "id": item.get("id", "widget"),
                    "title": item.get("title", "Widget"),
                })

        # -----------------------------------------------------
        # Render page
        # -----------------------------------------------------
        context = processor.decorate({"page": page, "result": result}, request)
        return render(request, self.template_name, context)
