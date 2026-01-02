import uuid


class BaseWidget:
    """
    Base class for all widgets.
    Lambdas must return dicts, so .to_dict() produces the exact structure.
    """

    widget_type = None     # override in subclasses
    template = None        # override in subclasses

    def __init__(self, title):
        self.id = f"w_{uuid.uuid4().hex}"
        self.title = title

    def base_dict(self):
        """Common fields included in every widget."""
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


class BaseChartWidget(BaseWidget):
    """
    Base class for all chart widgets.
    """
    widget_type = None
    template = "chart"

    def __init__(self, title, labels, datasets, options=None):
        super().__init__(title)
        self.labels = labels
        self.datasets = datasets
        self.options = options or {}

    def to_dict(self):
        d = self.base_dict()
        d.update({
            "labels": self.labels,
            "datasets": self.datasets,
            "options": self.options,
        })
        return d


class BarChartWidget(BaseChartWidget):
    widget_type = "bar"

    def __init__(self, title, labels, datasets, stacked=False, options=None):
        options = options or {}

        if stacked:
            # Ensure stacking is enabled
            scales = options.setdefault("scales", {})
            scales.setdefault("x", {})["stacked"] = True
            scales.setdefault("y", {})["stacked"] = True

        super().__init__(title, labels, datasets, options)


class LineChartWidget(BaseChartWidget):
    widget_type = "line"


class PieChartWidget(BaseChartWidget):
    widget_type = "pie"

    def __init__(self, title, labels, data, background_colors=None, options=None):
        datasets = [{
            "data": data,
            "backgroundColor": background_colors or [],
        }]
        super().__init__(title, labels, datasets, options)


class DoughnutChartWidget(BaseChartWidget):
    """
    A doughnut chart widget (like a pie chart but with a center cutout).
    """
    widget_type = "doughnut"

    def __init__(self, title, labels, data, options=None):
        datasets = [{
            "data": data
        }]
        super().__init__(title, labels, datasets, options)


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
            "error": self.error,
            "template": self.template,
        }

class CalendarWidget(BaseWidget):
    """
    A calendar widget that displays events on specific dates.
    Expected structure:
      - events: list of dicts like:
            {
                "date": "2025-01-15",
                "title": "Meeting with team",
                "description": "Discuss Q1 roadmap"
            }
    """
    widget_type = "calendar"
    template = "calendar"

    def __init__(self, title, events=None, view="month"):
        """
        :param title: Widget title
        :param events: List of event dicts
        :param view: "month", "week", or "day"
        """
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

