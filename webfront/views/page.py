# webfront/views.py

import json
from django.views import View
from django.shortcuts import render, get_object_or_404

from ravioli.builder import GraphBuilder
from oya.page import PageProcessor

from webfront.models import DynamicPage
from webfront.widgets import ErrorWidget
from webfront.factory import WidgetFactory


class DynamicPageView(View):
    template_name = "webfront/page.html"

    # -----------------------------------------------------
    # Page lambda
    # -----------------------------------------------------

    def run_page_lambda(self, page, request, graph, result):
        """
        Executes the page-level lambda.
        It MUST return a dict (context) for widget lambdas.
        """
        if not page.lambda_node:
            return {}

        try:
            output = page.lambda_node.execute(
                {"request": request, "G": graph}
            ) or {}

            if not isinstance(output, dict):
                raise ValueError("Page lambda must return a dict")

            return output

        except Exception as e:
            result.append(
                ErrorWidget("Page Lambda Error", str(e)).to_dict()
            )
            return {}

    # -----------------------------------------------------
    # Widget lambda
    # -----------------------------------------------------

    def run_widget_lambda(self, widget, request, input_data, result):
        """
        Execute widget lambda and return dict.
        """
        if not widget.lambda_node:
            return {}

        try:
            data = widget.lambda_node.execute(
                {"request": request, "data": input_data}
            ) or {}

            if not isinstance(data, dict):
                raise ValueError("Widget lambda must return a dict")

            return data

        except Exception as e:
            result.append(
                ErrorWidget(widget.title, f"Widget Error: {str(e)}").to_dict()
            )
            return {}

    # -----------------------------------------------------
    # Main view
    # -----------------------------------------------------

    def get(self, request, slug):
        page = get_object_or_404(DynamicPage, slug=slug)
        processor = PageProcessor()

        result = []
        graph = None

        # -------------------------------------------------
        # Build graph
        # -------------------------------------------------
        if not page.cypher_query:
            result.append(ErrorWidget("Query Error", "No Cypher").to_dict())
        else:
            graph = GraphBuilder.build_graph(page.cypher_query.query)

        # -------------------------------------------------
        # Page-level lambda (fan-out ETL)
        # -------------------------------------------------
        page_data = {}
        if graph is not None:
            page_data = self.run_page_lambda(page, request, graph, result)

        # -------------------------------------------------
        # Render widgets from DB
        # -------------------------------------------------
        for widget in page.widgets.order_by("order"):
            widget_data = self.run_widget_lambda(widget, request, page_data, result)

            # Convert dict → proper widget object
            widget_obj = WidgetFactory.from_dict(widget, widget_data)
            widget_data = widget_obj.to_dict()
            widget_data["data"] = json.dumps(widget_data)
            # Convert widget object → final dict for frontend
            result.append(widget_data)

        # -------------------------------------------------
        # Render page
        # -------------------------------------------------
        context = processor.decorate(
            {"page": page, "result": result},
            request,
        )
        return render(request, self.template_name, context)
