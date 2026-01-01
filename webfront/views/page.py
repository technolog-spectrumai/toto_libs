from django.views import View
from django.shortcuts import render, get_object_or_404
from ravioli.builder import GraphBuilder
from oya.page import PageProcessor
from webfront.models import DynamicPage
from webfront.widgets import TableWidget, ChartWidget, ErrorWidget
import json


# ---------------------------------------------------------
# Helper: format an error widget
# ---------------------------------------------------------

def make_error_widget(page, title, error):
    return {
        "id": f"error_{page.id}",
        "title": title,
        "type": "error",
        "template": "webfront/partials/error.html",
        "error": str(error),
        "data": None,
    }


# ---------------------------------------------------------
# Helper: normalize any widget from lambda
# ---------------------------------------------------------

def build_widget(item):
    widget_type = item.get("type")

    template_map = {
        "table": "webfront/partials/table.html",
        "bar": "webfront/partials/chart.html",
        "chart": "webfront/partials/chart.html",
        "error": "webfront/partials/error.html",
    }

    template_name = template_map.get(widget_type, "webfront/partials/error.html")

    widget = {
        "id": item.get("id", "widget"),
        "title": item.get("title", "Widget"),
        "type": widget_type,
        "template": template_name,
        "data": json.dumps(item)
    }
    widget.update(item)

    # Flatten fields (columns, rows, labels, datasets, options, etc.)
    # for key, value in item.items():
    #     if key not in ("id", "title", "type"):
    #         widget[key] = value

    return widget


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
        graph = None

        # -----------------------------------------------------
        # Build graph
        # -----------------------------------------------------
        if not page.cypher_query:
            result.append(make_error_widget(page, "Query Error", "No Cypher"))
        else:
            graph = GraphBuilder.build_graph(page.cypher_query.query)

        # -----------------------------------------------------
        # Execute lambda with widget classes injected
        # -----------------------------------------------------
        if page.lambda_node and graph:
            try:
                lambda_result = page.lambda_node.execute(
                    {"request": request, "G": graph},
                    extra_dependencies={
                        "TableWidget": TableWidget,
                        "ChartWidget": ChartWidget,
                        "ErrorWidget": ErrorWidget,
                    }
                ) or []
            except Exception as e:
                result.append(make_error_widget(page, "Lambda Error", e))
                lambda_result = []

        # -----------------------------------------------------
        # Validate lambda output
        # -----------------------------------------------------
        if not isinstance(lambda_result, list):
            result.append(make_error_widget(
                page,
                "Invalid Lambda Output",
                "Page lambda must return a list of widgets"
            ))
        else:
            for item in lambda_result:
                if "error" in item:
                    result.append(make_error_widget(
                        page,
                        item.get("title", "Error"),
                        item["error"]
                    ))
                else:
                    result.append(build_widget(item))
        p =0
        # -----------------------------------------------------
        # Render page
        # -----------------------------------------------------
        context = processor.decorate({"page": page, "result": result}, request)
        return render(request, self.template_name, context)
