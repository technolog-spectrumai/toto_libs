"""The settings page's forms.

Identity and prompt are two forms rather than one, because they are two tabs
and each POSTs alone: a half-finished persona must not be able to block a
one-word fix to a name, and the page tells you which tab saved. Both write the
same row.

The provider form carries the same write-only key field as the admin's — a
non-model ``PasswordInput(render_value=False)``, so the key never round-trips
into a response after it is submitted. The storing sequence itself lives in
``services.store_api_key`` and is shared with the admin.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import AiAgent, AiPersonalization, AiProvider


class ProviderForm(forms.ModelForm):
    """The provider row, plus one field that is not on the model.

    The key field is write-only by construction: not a model field, a password
    widget that never renders a value, and nothing anywhere puts the submitted
    value back into a page. What a reader gets instead is the status line —
    "set · active · rotated <date>" — which is everything an operator needs and
    nothing an attacker wants.
    """

    new_api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False,
                                   attrs={"autocomplete": "new-password"}),
        label=_("Set / replace API key"),
        help_text=_("Leave blank to keep the current one. Stored encrypted in "
                    "Steven's vault and never displayed again — not even here."),
    )

    class Meta:
        model = AiProvider
        fields = ("label", "base_url", "model", "temperature",
                  "max_output_tokens", "timeout", "active")


class PersonalizationForm(forms.ModelForm):
    """One user's standing note. Edited on the console, sent with every question."""

    class Meta:
        model = AiPersonalization
        fields = ["text"]
        widgets = {
            "text": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": _("Keep answers short. I write Django. "
                                 "Answer in Polish.")}),
        }
        labels = {"text": _("Personalization")}


class IdentityForm(forms.ModelForm):
    """Who it is. What a user sees before it has answered anything."""

    class Meta:
        model = AiAgent
        fields = ["name", "icon", "tagline", "description", "active"]
        widgets = {
            "tagline": forms.TextInput(attrs={
                "placeholder": _("Here to help you write.")}),
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            # A blank name would put "You are ." at the top of every system
            # message and leave the panel with an empty header.
            raise forms.ValidationError(_("The assistant needs a name."))
        return name

    def clean_icon(self):
        """A Font Awesome class, and nothing that can leave the class attribute.

        This value is rendered into ``class="{{ agent.icon }}"``. Django escapes
        quotes there, so this is not the thing standing between a page and an
        injection — it is here so a typo shows up as a validation error on the
        form rather than as a silently broken icon on six editor toolbars.
        """
        icon = (self.cleaned_data.get("icon") or "").strip()
        if not icon:
            return AiAgent._meta.get_field("icon").default
        if not all(part.replace("-", "").isalnum() for part in icon.split()):
            raise forms.ValidationError(
                _("A Font Awesome class, like 'fa-solid fa-robot'."))
        return icon


class PromptForm(forms.ModelForm):
    """How it writes. Everything here is sent to the model on every call.

    The per-kind notes are **built from the surfaces this host actually
    registered**, not from a fixed list of columns. zenobia has prose, sheets
    and cells; placidia has code and LaTeX; a host that installs neither editor
    gets no note fields at all rather than five boxes describing editors nobody
    can open. That is why ``kind_notes`` is a JSON column: the vocabulary
    belongs to the registry, and freezing it into a migration would mean a new
    editor could not bring a new kind with it.
    """

    class Meta:
        model = AiAgent
        fields = ["persona", "language", "house_rules"]
        widgets = {
            "persona": forms.Textarea(attrs={
                "rows": 3,
                "placeholder": _("You are the writing assistant on this "
                                 "platform. You are precise and brief.")}),
            "language": forms.TextInput(attrs={
                "placeholder": _("blank — answer in the language of the text")}),
            "house_rules": forms.Textarea(attrs={
                "rows": 5,
                "placeholder": _("Never invent a fact, a citation or an API. "
                                 "Say so plainly when you are unsure.")}),
        }

    #: Prefix for the dynamically built note fields.
    NOTE_PREFIX = "note__"

    def __init__(self, *args, kinds=(), **kwargs):
        """``kinds`` is ``(kind, where_it_applies)`` pairs, from the registry."""
        super().__init__(*args, **kwargs)
        self.kinds = tuple(kinds)
        notes = getattr(self.instance, "kind_notes", None)
        if not isinstance(notes, dict):
            notes = {}
        for kind, where in self.kinds:
            self.fields[f"{self.NOTE_PREFIX}{kind}"] = forms.CharField(
                label=_("Extra note for %(kind)s") % {"kind": kind},
                required=False,
                initial=notes.get(kind, ""),
                help_text=(_("Sent only with: %(where)s.") % {"where": where}
                           if where else ""),
                widget=forms.Textarea(attrs={"rows": 3}))

    @property
    def note_fields(self):
        """The note fields, for a template that renders them as their own group."""
        return [self[name] for name in self.fields
                if name.startswith(self.NOTE_PREFIX)]

    def clean(self):
        cleaned = super().clean()
        # Rebuilt rather than merged: a kind whose note was emptied must go
        # away, and a kind this host no longer has must not linger in the
        # column and keep being sent to the model.
        notes = {}
        for kind, _where in self.kinds:
            value = (cleaned.get(f"{self.NOTE_PREFIX}{kind}") or "").strip()
            if value:
                notes[kind] = value
        self.instance.kind_notes = notes
        return cleaned

    def save(self, commit=True):
        agent = super().save(commit=False)
        agent.kind_notes = self.instance.kind_notes
        if commit:
            agent.save()
        return agent
