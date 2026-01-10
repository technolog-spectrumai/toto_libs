import uuid


class BaseWidget:
    """
    Base class for all widgets.
    Lambdas must return dicts, so .to_dict() produces the exact structure.
    """

    widget_type = None
    template = None

    def __init__(self, title):
        self.id = f"w_{uuid.uuid4().hex}"
        self.title = title

    def base_dict(self):
        return {
            "type": self.widget_type,
            "id": self.id,
            "title": self.title,
            "template": self.template,
        }

    def to_dict(self):
        raise NotImplementedError("Subclasses must implement to_dict()")


# ---------------------------------------------------------
# TABLE WIDGET
# ---------------------------------------------------------

class TableWidget(BaseWidget):
    widget_type = "table"
    template = "table"

    def __init__(self, title, columns, rows):
        super().__init__(title)
        self.columns = columns
        self.rows = rows

    def to_dict(self):
        d = self.base_dict()
        d.update({
            "columns": self.columns,
            "rows": self.rows,
        })
        return d


# ---------------------------------------------------------
# GENERIC CHART WIDGET
# ---------------------------------------------------------

class ChartWidget(BaseWidget):
    """
    Generic chart widget.
    Lambdas must return:
    {
        "chart_type": "bar" | "line" | "pie" | "doughnut" | ...,
        "labels": [...],
        "datasets": [...],
        "options": {...}
    }
    """
    widget_type = "chart"
    template = "chart"

    def __init__(self, title, chart_type, labels, datasets, options=None):
        super().__init__(title)
        self.chart_type = chart_type
        self.labels = labels
        self.datasets = datasets
        self.options = options or {}

    def to_dict(self):
        d = self.base_dict()
        d.update({
            "chart_type": self.chart_type,
            "labels": self.labels,
            "datasets": self.datasets,
            "options": self.options,
        })
        return d


# ---------------------------------------------------------
# ERROR WIDGET
# ---------------------------------------------------------

class ErrorWidget(BaseWidget):
    widget_type = "error"
    template = "error"

    def __init__(self, title, error):
        super().__init__(title)
        self.error = str(error)

    def to_dict(self):
        return {
            "type": self.widget_type,
            "id": self.id,
            "title": self.title,
            "text": self.error,
            "template": self.template,
        }


# ---------------------------------------------------------
# CALENDAR WIDGET
# ---------------------------------------------------------

class CalendarWidget(BaseWidget):
    widget_type = "calendar"
    template = "calendar"

    def __init__(self, title, events=None, view="month"):
        super().__init__(title)
        self.events = events or []
        self.view = view

    def to_dict(self):
        d = self.base_dict()
        d.update({
            "events": self.events,
            "view": self.view,
        })
        return d


# ---------------------------------------------------------
# MAP WIDGET
# ---------------------------------------------------------

class MapWidget(BaseWidget):
    widget_type = "map"
    template = "map"

    def __init__(self, title, features=None, center=None, zoom=6, options=None):
        super().__init__(title)
        self.features = features or []
        self.center = center or [52.0, 19.0]
        self.zoom = zoom
        self.options = options or {}

    def to_dict(self):
        d = self.base_dict()
        d.update({
            "features": self.features,
            "center": self.center,
            "zoom": self.zoom,
            "options": self.options,
        })
        return d
