"""Reports: the definition syntax, the renderer, and what a report node files.

Named tests_more_reports.py (sibling of tests.py) and meant for the gate's
host-owned block beside the other workflows modules.
"""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from .models import (
    Report,
    ReportPage,
    ReportTemplate,
    Workflow,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
    report_definition_type,
    validate_report_definition,
)
from .services import reports

TABLE = {"type": "table", "title": "Rows", "data": {"path": "rows"},
         "columns": [{"key": "name", "label": "Name"},
                     {"key": "value", "label": "Value"}]}


class DefinitionValidationTests(SimpleTestCase):
    def assertRefused(self, definition, fragment):
        with self.assertRaises(ValidationError) as ctx:
            validate_report_definition(definition)
        self.assertIn(fragment, " ".join(ctx.exception.messages))

    def test_a_single_block_definition_is_accepted(self):
        validate_report_definition(TABLE)
        validate_report_definition({"type": "text", "body": "hello"})
        validate_report_definition({"type": "card", "value": {"path": "n"}})

    def test_a_one_page_one_block_definition_is_accepted(self):
        validate_report_definition(
            {"pages": [{"key": "p", "blocks": [{"type": "chart", "chart": "pie"}]}]})

    def test_it_must_be_an_object(self):
        self.assertRefused(["not", "an", "object"], "JSON object")

    def test_an_unknown_block_type_is_refused_and_the_valid_ones_named(self):
        self.assertRefused({"type": "gauge"}, "card, chart, table, text")

    def test_a_table_needs_columns_and_each_column_needs_a_key_or_path(self):
        self.assertRefused({"type": "table"}, "non-empty columns")
        self.assertRefused({"type": "table", "columns": []}, "non-empty columns")
        self.assertRefused({"type": "table", "columns": [{"label": "x"}]},
                           "key or path")
        validate_report_definition({"type": "table", "columns": [{"path": "a.b"}]})

    def test_an_unknown_chart_kind_is_refused(self):
        self.assertRefused({"type": "chart", "chart": "radar"}, "bar, line, pie")

    def test_exactly_one_page_with_exactly_one_block(self):
        block = {"type": "text"}
        self.assertRefused({"pages": []}, "exactly one page")
        self.assertRefused({"pages": [{"blocks": [block]}, {"blocks": [block]}]},
                           "exactly one page")
        self.assertRefused({"pages": [{"key": "p", "blocks": []}]},
                           "exactly one block")
        self.assertRefused({"pages": [{"key": "p", "blocks": [block, block]}]},
                           "exactly one block")
        self.assertRefused({"pages": ["page"]}, "must be an object")

    def test_the_type_is_read_through_pages(self):
        self.assertEqual(report_definition_type(
            {"pages": [{"blocks": [{"type": "card"}]}]}), "card")
        self.assertEqual(report_definition_type({"type": "chart"}), "chart")
        self.assertEqual(report_definition_type({}), "table")
        self.assertEqual(report_definition_type("garbage"), "table")


class SlugTests(TestCase):
    def test_workflow_slugs_are_unique_and_never_empty(self):
        first = Workflow.objects.create(name="Nightly Sync")
        second = Workflow.objects.create(name="Nightly Sync")
        blank = Workflow.objects.create(name="!!!")
        self.assertEqual(first.slug, "nightly-sync")
        self.assertEqual(second.slug, "nightly-sync-1")
        self.assertEqual(blank.slug, "workflow")

    def test_a_template_takes_its_type_from_its_definition(self):
        template = ReportTemplate.objects.create(
            name="Pie", report_type="table",
            definition={"type": "chart", "chart": "pie"})
        self.assertEqual(template.report_type, "chart")
        self.assertEqual(template.slug, "pie")

    def test_report_slugs_are_unique_per_title(self):
        a = Report.objects.create(title="Weekly", definition=TABLE)
        b = Report.objects.create(title="Weekly", definition=TABLE)
        self.assertNotEqual(a.slug, b.slug)


class ResolveBlockTests(SimpleTestCase):
    def test_a_card_resolves_its_value_by_path_with_a_default(self):
        block = {"type": "card", "title": "Total",
                 "value": {"path": "metrics.total"},
                 "subtitle": {"path": "metrics.missing", "default": "n/a"},
                 "format": "number"}
        out = reports.resolve_block(block, {"metrics": {"total": 12}})
        self.assertEqual((out["value"], out["subtitle"], out["format"]),
                         (12, "n/a", "number"))
        self.assertEqual(out["span"], 12)
        self.assertEqual(out["tone"], "neutral")

    def test_a_table_resolves_rows_and_columns_by_key_or_path(self):
        block = {"type": "table", "data": {"path": "rows"},
                 "columns": [{"key": "name"}, {"path": "stats.n"}, {"label": "blank"}]}
        data = {"rows": [{"name": "a", "stats": {"n": 1}}, {"name": "b"}]}
        out = reports.resolve_block(block, data)
        self.assertEqual([r["values"] for r in out["rows"]],
                         [["a", 1, ""], ["b", "", ""]])

    def test_a_single_object_where_rows_were_expected_is_one_row(self):
        block = {"type": "table", "rows": {"path": "only"}, "columns": [{"key": "k"}]}
        out = reports.resolve_block(block, {"only": {"k": "v"}})
        self.assertEqual([r["values"] for r in out["rows"]], [["v"]])
        missing = reports.resolve_block(block, {})
        self.assertEqual(missing["rows"], [])

    def test_a_bar_chart_scales_to_the_largest_value(self):
        block = {"type": "chart", "data": {"path": "series"}}
        data = {"series": [{"label": "a", "value": 5}, {"label": "b", "value": 10},
                           {"label": "c", "value": "n/a"}]}
        out = reports.resolve_block(block, data)
        self.assertEqual(out["chart"], "bar")
        self.assertEqual([p["percent"] for p in out["points"]], [50, 100, 0])
        self.assertEqual([p["share_percent"] for p in out["points"]], [33.3, 66.7, 0])
        self.assertIsNone(out["points"][2]["numeric_value"])
        self.assertEqual([t["label"] for t in out["y_ticks"]], ["10", "5", "0"])

    def test_custom_axes_and_fractional_ticks(self):
        block = {"type": "chart", "chart": "line", "series": {"path": "s"},
                 "x": "day", "y": "n"}
        out = reports.resolve_block(block, {"s": [{"day": "mon", "n": 1.5},
                                                  {"day": "tue", "n": 3}]})
        self.assertEqual([p["label"] for p in out["points"]], ["mon", "tue"])
        self.assertEqual([t["label"] for t in out["y_ticks"]], ["3", "1.5", "0"])
        # First point at the left edge, last at the right.
        self.assertEqual(out["line_points"].split()[0].split(",")[0], "0.0")
        self.assertEqual(out["line_points"].split()[-1].split(",")[0], "100.0")

    def test_an_empty_chart_has_one_zero_tick_and_no_gradient(self):
        out = reports.resolve_block({"type": "chart", "data": {"path": "s"}}, {})
        self.assertEqual(out["points"], [])
        self.assertEqual(out["pie_gradient"], "")
        self.assertEqual(out["y_ticks"], [{"label": "0", "y": 60}])

    def test_a_pie_gradient_closes_at_one_hundred_percent(self):
        out = reports.resolve_block(
            {"type": "chart", "chart": "pie", "data": {"path": "s"}},
            {"s": [{"label": "a", "value": 1}, {"label": "b", "value": 2}]})
        gradient = out["pie_gradient"]
        self.assertTrue(gradient.startswith("conic-gradient("))
        self.assertIn("0.0% 33.3%", gradient)
        self.assertTrue(gradient.endswith("100.0%)"))

    def test_a_text_block_takes_body_or_text(self):
        self.assertEqual(reports.resolve_block({"type": "text", "body": "hi"}, {})["body"], "hi")
        self.assertEqual(
            reports.resolve_block({"text": {"path": "t"}}, {"t": "via path"})["body"],
            "via path")

    def test_span_is_clamped_between_one_and_twelve(self):
        span = lambda v: reports.resolve_block({"type": "text", "span": v}, {})["span"]
        self.assertEqual([span(0), span(-3), span(6), span(40), span("x")],
                         [12, 1, 6, 12, 12])


def make_report_node_run(*, definition=TABLE, config=None, input_data=None,
                         template_name=None):
    count = ReportTemplate.objects.count()
    template = ReportTemplate.objects.create(
        name=template_name or f"T{count}", definition=definition)
    workflow = Workflow.objects.create(name="wf")
    node = WorkflowNode.objects.create(
        workflow=workflow, node_type=WorkflowNode.REPORT, label="Report here",
        report_template=template, config=config or {})
    run = WorkflowRun.objects.create(workflow=workflow)
    return WorkflowNodeRun.objects.create(workflow_run=run, node=node,
                                          input_data=input_data or {})


class ReportNodeServiceTests(TestCase):
    def test_title_comes_from_a_field_then_config_then_template(self):
        from_field = make_report_node_run(
            config={"title_field": "data.headline", "title": "fallback"},
            input_data={"data": {"headline": "From the data"}})
        from_config = make_report_node_run(
            config={"title_field": "data.absent", "title": "From config"},
            input_data={"data": {}})
        from_template = make_report_node_run(input_data={},
                                             template_name="The template")

        reports.create_report_from_node_run(from_field)
        reports.create_report_from_node_run(from_config)
        reports.create_report_from_node_run(from_template)

        self.assertEqual(sorted(Report.objects.values_list("title", flat=True)),
                         ["From config", "From the data", "The template"])

    def test_data_field_picks_a_subtree_and_scalars_are_wrapped(self):
        nr = make_report_node_run(config={"data_field": "data.stats"},
                                  input_data={"data": {"stats": {"rows": [1]}}})
        scalar = make_report_node_run(config={"data_field": "data.count"},
                                      input_data={"data": {"count": 7}})

        reports.create_report_from_node_run(nr)
        reports.create_report_from_node_run(scalar)

        self.assertEqual(Report.objects.get(source_node_run=nr).data, {"rows": [1]})
        self.assertEqual(Report.objects.get(source_node_run=scalar).data, {"value": 7})

    def test_the_report_records_where_it_came_from(self):
        nr = make_report_node_run(input_data={"data": {"rows": []}})

        out = reports.create_report_from_node_run(nr)

        report = Report.objects.get()
        self.assertEqual(report.metadata["source_node_run_id"], nr.id)
        self.assertEqual(report.metadata["source_node_label"], "Report here")
        self.assertEqual(report.metadata["workflow_run_id"], nr.workflow_run_id)
        self.assertEqual(out["data"]["report"]["slug"], report.slug)
        self.assertEqual(out["routes"], [])

    def test_routes_come_from_config_as_strings(self):
        nr = make_report_node_run(config={"routes": [1, "b"]})
        single = make_report_node_run(config={"route": "done"})
        self.assertEqual(reports.create_report_from_node_run(nr)["routes"], ["1", "b"])
        self.assertEqual(reports.create_report_from_node_run(single)["routes"], ["done"])

    def test_a_single_block_definition_makes_one_page_holding_it(self):
        nr = make_report_node_run(input_data={"data": {"rows": [{"name": "x"}]}})
        reports.create_report_from_node_run(nr)

        page = ReportPage.objects.get()
        self.assertEqual(page.key, "table")
        self.assertEqual(page.title, "Rows")
        self.assertEqual(page.blocks, [TABLE])
        self.assertEqual(page.data, {"rows": [{"name": "x"}]})

    def test_a_paged_definition_honours_data_path(self):
        definition = {"pages": [{"key": "Summary Page", "data_path": "summary",
                                 "blocks": [{"type": "card", "value": {"path": "n"}}]}]}
        nr = make_report_node_run(definition=definition,
                                  input_data={"data": {"summary": {"n": 3}}})
        reports.create_report_from_node_run(nr)

        page = ReportPage.objects.get()
        self.assertEqual(page.key, "summary-page")
        self.assertEqual(page.title, "Summary Page")
        self.assertEqual(page.data, {"n": 3})
        rendered = reports.render_report(page.report)
        self.assertEqual(rendered[0]["blocks"][0]["value"], 3)
