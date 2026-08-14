"""The staff form that turns a proposal into a formal vote.

A plain Form, not a ModelForm: half the inputs (electorate key, the choices
textarea) are not Question columns, and the model's own save() supplies the
invariants (FINAL, slug). The field-class constants mirror
``toto.events.forms`` so the page dresses like every other form.
"""

from __future__ import annotations

from django import forms
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from . import electorates
from .core import Visibility
from .models import Choice, Kind, Question

FIELD_CLASS = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none "
    "shadow-inner transition focus:ring-2 focus:ring-current/20"
)

FIELD_THEME_CLASS = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark placeholder:text-text-main-dark/45' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light placeholder:text-text-main-light/45'"
)

MAX_CHOICES = 10


class VoteCreateForm(forms.Form):
    title = forms.CharField(max_length=150, label=_("Title"))
    question_text = forms.CharField(
        max_length=300, label=_("The question"),
        help_text=_("The prompt voters answer, as one sentence."))
    body = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 6}),
        label=_("Proposal"),
        help_text=_("The case for and against. Shown above the options."))
    electorate = forms.ModelChoiceField(
        queryset=None, label=_("Electorate"),
        help_text=_("Who may ballot, and with what weight. Its membership "
                    "and weights are frozen when the vote opens."))
    rule = forms.ModelChoiceField(
        queryset=None, required=False, label=_("Consensus rule"),
        help_text=_("The threshold this vote must exceed. Its name and "
                    "percentage are snapshotted when the vote opens."))
    decision_header = forms.CharField(
        max_length=200, label=_("Decision header"),
        help_text=_("One sentence naming what deciding this means. Locked "
                    "once voting starts."))
    decision_comment = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 4}),
        label=_("Comment"),
        help_text=_("Optional context, shown behind Details. Locked with "
                    "the header."))
    opens_at = forms.SplitDateTimeField(
        label=_("Opens"),
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date"}, time_attrs={"type": "time"},
            date_format="%Y-%m-%d", time_format="%H:%M"))
    closes_at = forms.SplitDateTimeField(
        required=False, label=_("Closes"),
        help_text=_("Leave empty for a vote that stays open until staff "
                    "close it."),
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date"}, time_attrs={"type": "time"},
            date_format="%Y-%m-%d", time_format="%H:%M"))
    visibility = forms.ChoiceField(
        choices=Visibility.CHOICES, initial=Visibility.ON_CLOSE,
        label=_("Result visibility"),
        help_text=_("A running tally on a formal vote is an instrument for "
                    "changing its outcome — on-close is the honest default."))
    choices_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5}), label=_("Options"),
        help_text=_("One per line: a label, or “Label: description”. "
                    "Two to ten options."))

    SPLIT_FIELDS = ("opens_at", "closes_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Configured electorates for the global scope, and the staff-tuned
        # consensus rules. Both resolved at form time, never at import.
        from .electorate_models import ConsensusProfile, Electorate

        self.fields["electorate"].queryset = (
            Electorate.objects.in_scope("").active())
        self.fields["rule"].queryset = ConsensusProfile.objects.filter(
            is_active=True)
        # The events idiom: the form dresses its own widgets, the template
        # just renders {{ field }}.
        for name, field in self.fields.items():
            if name in self.SPLIT_FIELDS:
                for widget in field.widget.widgets:
                    widget.attrs.setdefault("class", FIELD_CLASS)
                    widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)
            else:
                field.widget.attrs.setdefault("class", FIELD_CLASS)
                field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)

    def clean_choices_text(self):
        parsed = []
        for line in self.cleaned_data["choices_text"].splitlines():
            line = line.strip()
            if not line:
                continue
            label, _sep, text = line.partition(":")
            parsed.append((label.strip()[:60], text.strip()[:300]))
        labels = [label for label, _text in parsed]
        if len(parsed) < 2:
            raise forms.ValidationError(
                _("A vote needs at least two options."))
        if len(parsed) > MAX_CHOICES:
            raise forms.ValidationError(
                _("At most %(max)s options.") % {"max": MAX_CHOICES})
        if len(set(labels)) != len(labels):
            raise forms.ValidationError(_("Option labels must be distinct."))
        return parsed

    def clean(self):
        cleaned = super().clean()
        opens, closes = cleaned.get("opens_at"), cleaned.get("closes_at")
        if opens and closes and closes <= opens:
            self.add_error("closes_at",
                           _("Voting must close after it opens."))
        return cleaned

    @transaction.atomic
    def save_vote(self, user) -> Question:
        """Create the vote and its options. The model supplies the rest:
        kind=VOTE forces FINAL in save(), the slug derives from the title."""
        data = self.cleaned_data
        from . import services

        question = Question.objects.create(
            kind=Kind.VOTE,
            title=data["title"],
            question_text=data["question_text"],
            body=data["body"],
            opens_at=data["opens_at"],
            closes_at=data["closes_at"],
            visibility=data["visibility"],
            created_by=user,
            decision_header=data["decision_header"],
            decision_comment=data["decision_comment"],
        )
        for position, (label, text) in enumerate(data["choices_text"]):
            Choice.objects.create(question=question, label=label, text=text,
                                  position=position)
        if data.get("rule"):
            services.snapshot_rule(question, data["rule"])
        # The register: membership and weights copied now, so later edits to
        # the electorate change future votes only.
        services.freeze_roll(question, electorate=data["electorate"])
        return question
