"""verbena's shared helpers, as the socialhub uses them (2026-09-29): the
slug helper behind news topics, the house field styling applied to member
forms, and the admin form that gives a section its rich-text editor.

The page/section views and the LaTeX module have no consumer on this host
and are deliberately not covered here."""

from django import forms
from django.test import SimpleTestCase, TestCase

from toto.people.models import Person
from toto.verbena.admin import make_section_form
from toto.verbena.forms import (
    CHECKBOX_CLASS,
    FIELD_CLASS,
    FIELD_THEME_CLASS,
    apply_oya_checkbox_styles,
    apply_oya_field_styles,
)
from toto.verbena.utils import unique_slug


class UniqueSlugTests(TestCase):
    def test_a_free_slug_is_the_slugified_value(self):
        self.assertEqual(unique_slug(Person(display_name="x"), "Board Minutes"), "board-minutes")

    def test_a_taken_slug_gets_a_number_from_two(self):
        Person.objects.create(display_name="a", slug="minutes")
        Person.objects.create(display_name="b", slug="minutes-2")
        self.assertEqual(unique_slug(Person(display_name="c"), "Minutes"), "minutes-3")

    def test_a_value_with_nothing_sluggable_falls_back(self):
        self.assertEqual(unique_slug(Person(display_name="x"), "Олена", fallback="topic"),
                         "topic")
        self.assertEqual(unique_slug(Person(display_name="x"), "!!!"), "item")

    def test_a_row_does_not_collide_with_itself(self):
        person = Person.objects.create(display_name="a", slug="minutes")
        self.assertEqual(unique_slug(person, "Minutes"), "minutes")

    def test_a_news_topic_takes_its_slug_this_way(self):
        from toto.socialhub.models import CommunityNewsTopic

        first = CommunityNewsTopic.objects.create(name="Budget")
        second = CommunityNewsTopic.objects.create(name="budget!")
        nameless = CommunityNewsTopic.objects.create(name="Бюджет")
        self.assertEqual((first.slug, second.slug, nameless.slug),
                         ("budget", "budget-2", "topic"))
        self.assertEqual(str(first), "Budget")


class _Form(forms.Form):
    title = forms.CharField()
    note = forms.CharField(widget=forms.TextInput(attrs={"class": "mine"}))
    agree = forms.BooleanField(required=False)


class FieldStyleTests(SimpleTestCase):
    def test_every_field_gets_the_house_classes_except_the_skipped(self):
        form = _Form()
        apply_oya_field_styles(form.fields, skip=("agree",))
        attrs = form.fields["title"].widget.attrs
        self.assertEqual(attrs["class"], FIELD_CLASS)
        self.assertEqual(attrs["x-bind:class"], FIELD_THEME_CLASS)
        self.assertNotIn("class", form.fields["agree"].widget.attrs)

    def test_a_field_s_own_class_is_kept(self):
        form = _Form()
        apply_oya_field_styles(form.fields)
        self.assertEqual(form.fields["note"].widget.attrs["class"], "mine")
        self.assertIn("x-bind:class", form.fields["note"].widget.attrs)

    def test_a_checkbox_gets_its_own_smaller_style(self):
        form = _Form()
        apply_oya_checkbox_styles(form.fields["agree"])
        self.assertEqual(form.fields["agree"].widget.attrs["class"], CHECKBOX_CLASS)

    def test_the_styles_reach_the_rendered_widget(self):
        form = _Form()
        apply_oya_field_styles(form.fields)
        self.assertIn('x-bind:class="darkMode', str(form["title"]))


class SectionFormTests(SimpleTestCase):
    def test_a_section_form_edits_its_content_in_the_rich_text_editor(self):
        from trix_editor.widgets import TrixEditorWidget

        from toto.socialhub.models import CommunityNewsPost

        form_class = make_section_form(CommunityNewsPost)
        self.assertIs(form_class._meta.model, CommunityNewsPost)
        self.assertIsInstance(form_class().fields["content"].widget, TrixEditorWidget)
        self.assertIn("title", form_class().fields)

    def test_each_model_gets_its_own_form(self):
        from toto.socialhub.models import CommunityNewsPost, CommunityNewsTopic

        self.assertIsNot(make_section_form(CommunityNewsPost), make_section_form(CommunityNewsTopic))
