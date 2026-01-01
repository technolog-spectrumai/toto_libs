from django.views import View
from django.shortcuts import render, get_object_or_404
from ravioli.builder import GraphBuilder
from oya.page import PageProcessor
from webfront.models import DynamicPage
from webfront.widgets import (TableWidget, ErrorWidget, BarChartWidget, LineChartWidget,
                              PieChartWidget, DoughnutChartWidget)
import json


def flatten_widget_data(item):
    widget_type = item.get("type")
    widget = {
        "id": item.get("id", "widget"),
        "title": item.get("title", "Widget"),
        "type": widget_type,
        "data": json.dumps(item),
        "template": item.get("template", "webfront/partials/error.html")
    }
    widget.update(item)

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
            result.append(ErrorWidget("Query Error", "No Cypher"))
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
                        "ErrorWidget": ErrorWidget,
                        "BarChartWidget": BarChartWidget,
                        "LineChartWidget": LineChartWidget,
                        "PieChartWidget": PieChartWidget,
                        "DoughnutChartWidget": DoughnutChartWidget
                    }
                ) or []
            except Exception as e:
                result.append(ErrorWidget(page, "Lambda Error", e))
                lambda_result = []

        # -----------------------------------------------------
        # Validate lambda output
        # -----------------------------------------------------
        if not isinstance(lambda_result, list):
            result.append(ErrorWidget(
                "Invalid Lambda Output",
                "Page lambda must return a list of widgets"
            ))
        else:
            for item in lambda_result:
                if "error" in item:
                    result.append(ErrorWidget(
                        item.get("title", "Error"),
                        item["error"]
                    ))
                else:
                    result.append(flatten_widget_data(item))
        p =0
        # -----------------------------------------------------
        # Render page
        # -----------------------------------------------------
        context = processor.decorate({"page": page, "result": result}, request)
        return render(request, self.template_name, context)
