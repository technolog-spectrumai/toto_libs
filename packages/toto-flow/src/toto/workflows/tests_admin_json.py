"""The workflows admin edits JSON in a plain box (2026-10-01, 37c.27).

django-jsoneditor's widget drew these staff-only fields, and the package
brought 1.7 MB of static (its own copy of ACE) into every image that
installed toto-flow, for nothing else. The fields are a monospace textarea
now, the stored value indented; Django's JSONField refuses text that is not
JSON as it always did, and gives back what was typed.

Named tests_admin_json.py (sibling of tests.py) and meant for the gate's
host-owned block beside the other workflows modules.

    manage.py test toto.workflows.tests_admin_json
"""

from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase

from . import admin as workflows_admin
from .admin import JsonTextarea
from .models import Report, ReportPage, ReportTemplate, WorkflowNode

#: The four admins that edit a JSON field.
JSON_ADMINS = (WorkflowNode, ReportTemplate, Report, ReportPage)


class JsonTextareaTests(SimpleTestCase):
    def test_a_stored_value_is_shown_indented(self):
        self.assertEqual(JsonTextarea().format_value('{"rows": [1, 2], "name": "Łódź"}'),
                         '{\n  "rows": [\n    1,\n    2\n  ],\n  "name": "Łódź"\n}')

    def test_text_that_is_not_json_is_given_back_as_typed(self):
        self.assertEqual(JsonTextarea().format_value('{"rows": [1, '), '{"rows": [1, ')

    def test_nothing_stored_is_an_empty_box(self):
        self.assertIsNone(JsonTextarea().format_value(None))
        self.assertIsNone(JsonTextarea().format_value(""))

    def test_it_is_a_monospace_textarea(self):
        html = JsonTextarea().render("definition", '{"a": 1}')
        self.assertTrue(html.startswith("<textarea"), html)
        self.assertIn("font-family: monospace;", html)
        self.assertIn('class="vLargeTextField"', html)


class WorkflowsAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = get_user_model().objects.create_superuser(
            "json-root", "json-root@example.org", "pw")

    def setUp(self):
        self.request = RequestFactory().get("/admin/")
        self.request.user = self.root

    def test_every_json_field_is_a_plain_box(self):
        for model in JSON_ADMINS:
            with self.subTest(model=model.__name__):
                model_admin = admin.site._registry[model]
                form = model_admin.get_form(self.request)()
                fields = [field for field in form.fields.values()
                          if isinstance(field, forms.JSONField)]
                self.assertTrue(fields)
                for field in fields:
                    self.assertIsInstance(field.widget, JsonTextarea)
                self.assertNotIn("jsoneditor", str(model_admin.media + form.media))

    def test_a_stored_definition_reads_indented_and_saves_unchanged(self):
        template = ReportTemplate.objects.create(name="Rows", slug="rows")
        form_class = admin.site._registry[ReportTemplate].get_form(self.request, template)
        form = form_class(instance=template)
        self.assertIn("\n  &quot;type&quot;: &quot;table&quot;", str(form["definition"]))
        shown = form.fields["definition"].widget.format_value(form["definition"].value())
        posted = form_class({"name": "Rows", "slug": "rows", "report_type": "table",
                             "description": "", "definition": shown}, instance=template)
        self.assertTrue(posted.is_valid(), posted.errors)
        self.assertEqual(posted.cleaned_data["definition"], template.definition)

    def test_text_that_is_not_json_is_refused_and_kept(self):
        template = ReportTemplate.objects.create(name="Rows", slug="rows")
        form_class = admin.site._registry[ReportTemplate].get_form(self.request, template)
        form = form_class({"name": "Rows", "slug": "rows", "report_type": "table",
                           "description": "", "definition": '{"pages": ['}, instance=template)
        self.assertFalse(form.is_valid())
        self.assertIn("definition", form.errors)
        self.assertIn('{&quot;pages&quot;: [', str(form["definition"]))

    def test_the_admin_names_nothing_from_the_package(self):
        self.assertFalse([name for name, value in vars(workflows_admin).items()
                          if getattr(value, "__module__", "").startswith("jsoneditor")])
