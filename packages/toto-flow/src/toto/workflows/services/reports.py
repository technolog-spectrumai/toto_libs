from typing import Any

from django.utils.text import slugify

from toto.core.connectors import get_dotted

from ..models import (
    Report,
    ReportPage,
    ReportTemplate,
    WorkflowNodeRun,
    report_definition_block,
    report_definition_type,
)


class ReportGenerationError(RuntimeError):
    pass


def create_report_from_node_run(node_run: WorkflowNodeRun) -> dict:
    node = node_run.node
    template = node.report_template
    if template is None:
        raise ReportGenerationError("Report node has no report_template configured.")

    config = node.config or {}
    input_data = node_run.input_data or {}
    report_data = _configured_data(config, input_data)
    title = _configured_title(config, input_data, template)
    metadata = {
        "workflow_id": node_run.workflow_run.workflow_id,
        "workflow_run_id": node_run.workflow_run_id,
        "source_node_run_id": node_run.id,
        "source_node_id": node.id,
        "source_node_label": node.label,
    }

    report = create_report(
        template=template,
        data=report_data,
        title=title,
        workflow_run=node_run.workflow_run,
        source_node_run=node_run,
        metadata=metadata,
    )
    return {
        "data": {
            "report": serialize_report_reference(report),
        },
        "routes": _configured_routes(config),
    }


def create_report(
    *,
    template: ReportTemplate,
    data: dict,
    title: str | None = None,
    workflow_run=None,
    source_node_run=None,
    metadata: dict | None = None,
) -> Report:
    definition = template.definition or {}
    report = Report.objects.create(
        template=template,
        workflow_run=workflow_run,
        source_node_run=source_node_run,
        title=title or template.name,
        report_type=template.report_type,
        definition=definition,
        data=data or {},
        metadata=metadata or {},
    )
    _create_pages(report)
    return report


def serialize_report_reference(report: Report) -> dict:
    return {
        "id": report.id,
        "title": report.title,
        "slug": report.slug,
        "report_type": report.report_type,
        "status": report.status,
        "page_count": report.pages.count(),
    }


def render_report(report: Report) -> list[dict]:
    pages = []
    for page in report.pages.all():
        pages.append(
            {
                "page": page,
                "blocks": [
                    resolve_block(block, page.data)
                    for block in (page.blocks or [])
                ],
            }
        )
    return pages


def resolve_block(block: dict, data: dict) -> dict:
    block_type = block.get("type", "text")
    resolved = {
        "type": block_type,
        "title": block.get("title", ""),
        "span": _span(block.get("span")),
        "tone": block.get("tone", "neutral"),
        "icon": block.get("icon", ""),
        "raw": block,
    }

    if block_type == "card":
        resolved.update(
            {
                "value": _resolve_value(block.get("value"), data),
                "subtitle": _resolve_value(block.get("subtitle"), data),
                "format": block.get("format", "text"),
            }
        )
    elif block_type == "table":
        rows = _resolve_collection(block.get("data") or block.get("rows"), data)
        columns = block.get("columns") or []
        resolved.update(
            {
                "columns": columns,
                "rows": [
                    {
                        "raw": row,
                        "values": [
                            _resolve_row_value(row, column)
                            for column in columns
                        ],
                    }
                    for row in rows
                ],
            }
        )
    elif block_type == "chart":
        rows = _resolve_collection(block.get("data") or block.get("series"), data)
        x_key = block.get("x", "label")
        y_key = block.get("y", "value")
        points = []
        numeric_values = []
        total_numeric = 0
        for row in rows:
            value = _resolve_path(row, y_key)
            numeric_value = _numeric(value)
            if numeric_value is not None:
                numeric_values.append(numeric_value)
                total_numeric += numeric_value
            points.append(
                {
                    "label": _resolve_path(row, x_key),
                    "value": value,
                    "numeric_value": numeric_value,
                }
            )
        max_value = max(numeric_values) if numeric_values else 0
        for point in points:
            point["percent"] = (
                int((point["numeric_value"] / max_value) * 100)
                if max_value and point["numeric_value"] is not None
                else 0
            )
            point["share_percent"] = (
                round((point["numeric_value"] / total_numeric) * 100, 1)
                if total_numeric and point["numeric_value"] is not None
                else 0
            )
        _decorate_chart_points(points)
        resolved.update(
            {
                "chart": block.get("chart", "bar"),
                "points": points,
                "line_points": _line_points(points),
                "pie_gradient": _pie_gradient(points),
                "y_ticks": _chart_y_ticks(max_value),
            }
        )
    else:
        resolved.update(
            {
                "body": _resolve_value(block.get("body") or block.get("text"), data),
            }
        )

    return resolved


def _create_pages(report: Report) -> None:
    if "pages" not in report.definition:
        block = report_definition_block(report.definition)
        page_data = _page_data(report.definition, report.data)
        ReportPage.objects.create(
            report=report,
            key=slugify(report.definition.get("key") or report.report_type) or report.report_type,
            title=report.definition.get("title") or report.title,
            order=0,
            blocks=[block],
            data=page_data,
        )
        report_type = report_definition_type(report.definition)
        if report.report_type != report_type:
            report.report_type = report_type
            report.save(update_fields=["report_type", "updated_at"])
        return

    pages = report.definition.get("pages") or []
    for index, page_def in enumerate(pages):
        key = slugify(page_def.get("key") or f"page-{index + 1}") or f"page-{index + 1}"
        page_data = _page_data(page_def, report.data)
        ReportPage.objects.create(
            report=report,
            key=key,
            title=page_def.get("title") or key.replace("-", " ").title(),
            order=int(page_def.get("order", index)),
            blocks=page_def.get("blocks") or [],
            data=page_data,
        )


def _page_data(definition: dict, report_data: dict) -> dict:
    page_data = (
        _resolve_value({"path": definition.get("data_path")}, report_data)
        if definition.get("data_path")
        else report_data
    )
    if not isinstance(page_data, dict):
        page_data = {"value": page_data}
    return page_data


def _configured_data(config: dict, input_data: dict) -> dict:
    data_field = config.get("data_field")
    if data_field:
        data = get_dotted(input_data, data_field, default={})
    else:
        data = input_data.get("data", input_data)
    if isinstance(data, dict):
        return data
    return {"value": data}


def _configured_title(config: dict, input_data: dict, template: ReportTemplate) -> str:
    if config.get("title_field"):
        title = get_dotted(input_data, config["title_field"], default="")
        if title:
            return str(title)
    if config.get("title"):
        return str(config["title"])
    return template.name


def _configured_routes(config: dict) -> list[str]:
    if "routes" in config:
        routes = config.get("routes") or []
        return [str(route) for route in routes]
    if config.get("route") is not None:
        return [str(config["route"])]
    return []


def _resolve_collection(spec, data: dict) -> list:
    value = _resolve_value(spec, data)
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _resolve_value(spec, data: dict):
    if isinstance(spec, dict) and "path" in spec:
        return get_dotted(data, spec.get("path") or "", default=spec.get("default"))
    return spec


def _resolve_row_value(row: Any, column: dict):
    path = column.get("path") or column.get("key")
    if path is None:
        return ""
    return _resolve_path(row, path)


def _resolve_path(value: Any, path: str):
    if isinstance(value, dict):
        return get_dotted(value, path, default="")
    return getattr(value, path, "")


def _numeric(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_CHART_COLORS = [
    "#2563eb",
    "#16a34a",
    "#f59e0b",
    "#dc2626",
    "#7c3aed",
    "#0891b2",
    "#db2777",
    "#475569",
]


def _decorate_chart_points(points: list[dict]) -> None:
    count = max(len(points) - 1, 1)
    for index, point in enumerate(points):
        point["color"] = _CHART_COLORS[index % len(_CHART_COLORS)]
        point["x_percent"] = round((index / count) * 100, 2)
        point["y_percent"] = round(60 - (float(point.get("percent") or 0) * 0.55), 2)


def _line_points(points: list[dict]) -> str:
    return " ".join(
        f"{point.get('x_percent', 0)},{point.get('y_percent', 60)}"
        for point in points
    )


def _pie_gradient(points: list[dict]) -> str:
    if not points:
        return ""
    cursor = 0
    segments = []
    for index, point in enumerate(points):
        share = float(point.get("share_percent") or 0)
        end = 100 if index == len(points) - 1 else min(cursor + share, 100)
        segments.append(f"{point['color']} {cursor:.1f}% {end:.1f}%")
        cursor = end
    return f"conic-gradient({', '.join(segments)})"


def _chart_y_ticks(max_value: float) -> list[dict]:
    values = [max_value, max_value / 2, 0] if max_value else [0]
    positions = [5, 32.5, 60] if max_value else [60]
    return [
        {"label": _format_tick(value), "y": position}
        for value, position in zip(values, positions)
    ]


def _format_tick(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _span(value) -> int:
    try:
        return min(max(int(value or 12), 1), 12)
    except (TypeError, ValueError):
        return 12
