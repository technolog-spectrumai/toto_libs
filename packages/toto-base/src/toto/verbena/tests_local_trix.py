"""Trix from the image's own copy, never unpkg (2026-10-01, 37c.20).

django-trix-editor's widget hard-codes its two files at unpkg.com, so the
community news editor and the admin's news form told unpkg the address of
everybody who wrote a post, though the image carries both files
(download_vendor.py, `vendor/trix/`). `LocalTrixEditorWidget` keeps the
package's upload script and the admin's dark-theme styles and takes Trix
from /static/. A form cannot ask for it in `Meta.widgets`: the field's own
form field (`TrixEditorField.formfield`) puts the package's widget back
whatever it is given, so `use_local_trix` swaps it on the built form. The
admin guard walks every registered model with a Trix field for the same
reason.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin
from django.test import RequestFactory, SimpleTestCase, TestCase
from trix_editor.fields import TrixEditorField
from trix_editor.widgets import TrixEditorWidget

from toto.verbena.admin import make_section_form
from toto.verbena.widgets import LocalTrixEditorWidget, use_local_trix

#: Where the package fetches Trix from, and the copies the image carries.
UNPKG = "unpkg.com"
LOCAL_FILES = ("vendor/trix/trix.umd.min.js", "vendor/trix/trix.css")


def rendered(media) -> str:
    return str(media)


class WidgetTests(SimpleTestCase):
    def test_trix_comes_from_the_platforms_static_files(self):
        media = rendered(LocalTrixEditorWidget().media)
        self.assertNotIn(UNPKG, media)
        for path in LOCAL_FILES:
            self.assertIn(f"/static/{path}", media)

    def test_the_upload_script_and_the_admins_styles_stay(self):
        media = rendered(LocalTrixEditorWidget().media)
        self.assertIn("/trix-editor/upload/", media)
        self.assertIn('addEventListener("trix-attachment-add"', media)
        self.assertIn("trix-toolbar .trix-button-group", media)

    def test_the_editor_element_is_the_packages(self):
        html = LocalTrixEditorWidget().render("content", "<p>Hi</p>", attrs={"id": "id_content"})
        self.assertIn('<trix-editor input="id_content"></trix-editor>', html)


class SwapTests(SimpleTestCase):
    def test_the_field_puts_the_packages_widget_back_whatever_it_is_given(self):
        """Why the swap is made on the built form: if the package ever honours
        the argument, `Meta.widgets` would do and this can go."""
        field = TrixEditorField().formfield(widget=LocalTrixEditorWidget)
        self.assertIs(type(field.widget), TrixEditorWidget)

    def test_a_trix_field_gets_the_local_widget_with_its_attributes(self):
        fields = {"content": TrixEditorField().formfield(),
                  "title": forms.CharField()}
        fields["content"].widget.attrs["data-x"] = "1"
        use_local_trix(fields)
        self.assertIsInstance(fields["content"].widget, LocalTrixEditorWidget)
        self.assertEqual(fields["content"].widget.attrs["data-x"], "1")
        self.assertIs(type(fields["title"].widget), forms.TextInput)

    def test_a_second_swap_changes_nothing(self):
        fields = {"content": TrixEditorField().formfield()}
        use_local_trix(fields)
        widget = fields["content"].widget
        use_local_trix(fields)
        self.assertIs(fields["content"].widget, widget)


class FormTests(SimpleTestCase):
    def test_the_community_news_editor_uses_the_local_copy(self):
        from toto.socialhub.forms import CommunityNewsPostForm

        form = CommunityNewsPostForm()
        self.assertIsInstance(form.fields["content"].widget, LocalTrixEditorWidget)
        self.assertNotIn(UNPKG, rendered(form.media))

    def test_a_section_form_uses_the_local_copy(self):
        from toto.socialhub.models import CommunityNewsPost

        form = make_section_form(CommunityNewsPost)()
        self.assertIsInstance(form.fields["content"].widget, LocalTrixEditorWidget)
        self.assertNotIn(UNPKG, rendered(form.media))


class AdminTests(TestCase):
    def test_no_admin_form_with_a_trix_field_loads_trix_from_unpkg(self):
        from django.contrib.auth import get_user_model

        request = RequestFactory().get("/admin/")
        request.user = get_user_model().objects.create_superuser("root", "r@example.org", "pw")
        checked = []
        for model, model_admin in admin.site._registry.items():
            if not any(isinstance(f, TrixEditorField) for f in model._meta.get_fields()):
                continue
            with self.subTest(model=model._meta.label):
                form = model_admin.get_form(request)()
                self.assertNotIn(UNPKG, rendered(form.media))
                checked.append(model._meta.label)
        self.assertIn("socialhub.CommunityNewsPost", checked, "vacuous: no Trix field found")
