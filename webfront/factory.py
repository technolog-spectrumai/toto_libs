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
    """

    # -----------------------------------------------------
    # DISPATCH MAP
    # -----------------------------------------------------
    BUILDERS = {
        "table": "_build_table",
        "chart": "_build_chart",
        "calendar": "_build_calendar",
        "map": "_build_map",
    }

    @staticmethod
    def from_dict(data):
        if not isinstance(data, dict):
            return ErrorWidget("Invalid Widget", "Lambda must return a dict")

        widget_type = data.get("type")
        title = data.get("title", "Untitled")

        # Look up builder method name
        method_name = WidgetFactory.BUILDERS.get(widget_type)

        if not method_name:
            return ErrorWidget(title, f"Unknown widget type: {widget_type}")

        try:
            # Dynamically call the builder
            method = getattr(WidgetFactory, method_name)
            return method(title, data)

        except Exception as e:
            return ErrorWidget(title, f"Widget build error: {str(e)}")

    # -----------------------------------------------------
    # BUILDERS
    # -----------------------------------------------------

    @staticmethod
    def _build_table(title, data):
        return TableWidget(
            title=title,
            columns=data.get("columns", []),
            rows=data.get("rows", []),
        )

    @staticmethod
    def _build_chart(title, data):
        return ChartWidget(
            title=title,
            chart_type=data.get("chart_type"),
            labels=data.get("labels", []),
            datasets=data.get("datasets", []),
            options=data.get("options", {}),
        )

    @staticmethod
    def _build_calendar(title, data):
        return CalendarWidget(
            title=title,
            events=data.get("events", []),
            view=data.get("view", "month"),
        )

    @staticmethod
    def _build_map(title, data):
        return MapWidget(
            title=title,
            features=data.get("features", []),
            center=data.get("center"),
            zoom=data.get("zoom", 6),
            options=data.get("options", {}),
        )
