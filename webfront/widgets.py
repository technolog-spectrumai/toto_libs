import json
import uuid


class BaseWidget:
    """
    Base class for all widgets.
    Lambdas must return dicts, so .to_dict() produces the exact structure.
    """

    widget_type = None  # override in subclasses

    def __init__(self, title):
        self.id = f"w_{uuid.uuid4().hex}"
        self.title = title

    def to_dict(self):
        """Return the exact dict structure expected by the frontend."""
        raise NotImplementedError("Subclasses must implement to_dict()")


# ---------------------------------------------------------
# TABLE WIDGET
# ---------------------------------------------------------

class TableWidget(BaseWidget):
    widget_type = "table"

    def __init__(self, title, columns, rows):
        super().__init__(title)
        self.columns = columns
        self.rows = rows

    def to_dict(self):
        return {
            "type": self.widget_type,
            "id": self.id,
            "title": self.title,
            "columns": self.columns,
            "rows": self.rows,
        }


# ---------------------------------------------------------
# CHART WIDGET (Bar, Line, Pie, etc.)
# ---------------------------------------------------------

class ChartWidget(BaseWidget):
    """
    Generic Chart.js widget.
    chart_type can be "bar", "line", "pie", etc.
    """

    widget_type = "bar"  # default

    def __init__(self, title, labels, datasets, options=None, chart_type=None):
        super().__init__(title)
        self.labels = labels
        self.datasets = datasets
        self.options = options or {}

        if chart_type:
            self.widget_type = chart_type

    def to_dict(self):
        return {
            "type": self.widget_type,
            "id": self.id,
            "title": self.title,
            "labels": self.labels,
            "datasets": self.datasets,
            "options": self.options,
        }


# ---------------------------------------------------------
# ERROR WIDGET
# ---------------------------------------------------------

class ErrorWidget(BaseWidget):
    widget_type = "error"

    def __init__(self, title, error):
        super().__init__(title)
        self.error = str(error)

    def to_dict(self):
        return {
            "type": self.widget_type,
            "id": self.id,
            "title": self.title,
            "error": self.error,
        }
