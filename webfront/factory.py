# webfront/factory.py

from webfront.widgets import (
    TableWidget,
    ChartWidget,
    CalendarWidget,
    MapWidget,
    ErrorWidget,
)


class WidgetFactory:
    """
    Converts a dict returned by a lambda into a proper widget object.
    The widget TYPE comes from the DB model, not from the lambda output.
    """

    BUILDERS = {
        "table": "_build_table",
        "chart": "_build_chart",
        "calendar": "_build_calendar",
        "map": "_build_map",
    }

    @staticmethod
    def from_dict(widget_model, data):
        """
        widget_model: DB Widget instance
        data: dict returned by lambda
        """
        if not isinstance(data, dict):
            return ErrorWidget(widget_model.title, "Lambda must return a dict")

        widget_type = widget_model.type
        title = widget_model.title

        # Look up builder
        method_name = WidgetFactory.BUILDERS.get(widget_type)
        if not method_name:
            return ErrorWidget(title, f"Unknown widget type in model: {widget_type}")

        try:
            method = getattr(WidgetFactory, method_name)
            return method(widget_model, data)

        except Exception as e:
            return ErrorWidget(title, f"Widget build error: {str(e)}")

    # -----------------------------------------------------
    # BUILDERS
    # -----------------------------------------------------

    @staticmethod
    def _build_table(widget_model, data):
        # Validate required fields
        if "columns" not in data or "rows" not in data:
            return ErrorWidget(widget_model.title, "Table widget missing columns/rows")

        return TableWidget(
            title=widget_model.title,
            columns=data["columns"],
            rows=data["rows"],
        )

    @staticmethod
    def _build_chart(widget_model, data):
        required = ["chart_type", "labels", "datasets"]
        for key in required:
            if key not in data:
                return ErrorWidget(widget_model.title, f"Chart widget missing '{key}'")

        return ChartWidget(
            title=widget_model.title,
            chart_type=data["chart_type"],
            labels=data["labels"],
            datasets=data["datasets"],
            options=data.get("options", {}),
        )

    @staticmethod
    def _build_calendar(widget_model, data):
        if "events" not in data:
            return ErrorWidget(widget_model.title, "Calendar widget missing events")

        return CalendarWidget(
            title=widget_model.title,
            events=data["events"],
            view=data.get("view", "month"),
        )

    @staticmethod
    def _build_map(widget_model, data):
        if "features" not in data:
            return ErrorWidget(widget_model.title, "Map widget missing features")

        return MapWidget(
            title=widget_model.title,
            features=data["features"],
            center=data.get("center"),
            zoom=data.get("zoom", 6),
            options=data.get("options", {}),
        )
