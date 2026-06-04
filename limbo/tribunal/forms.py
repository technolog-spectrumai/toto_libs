from django import forms

from toto.tribunal.models import (
    JurySession,
    JuryVote,
    TribunalCase,
    TribunalClaim,
    TribunalEvidence,
    TribunalParty,
    TribunalRuling,
)


class TribunalCaseForm(forms.ModelForm):
    class Meta:
        model = TribunalCase
        fields = [
            "title",
            "reason",
            "description",
            "opened_by",
            "assigned_to",
            "related_object",
            "related_order",
            "status",
            "priority",
            "resolved_at",
            "metadata",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "resolved_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


class TribunalPartyForm(forms.ModelForm):
    class Meta:
        model = TribunalParty
        fields = ["person", "role", "statement", "metadata"]
        widgets = {
            "statement": forms.Textarea(attrs={"rows": 3}),
        }


class TribunalClaimForm(forms.ModelForm):
    class Meta:
        model = TribunalClaim
        fields = [
            "claimant",
            "claim_type",
            "summary",
            "description",
            "requested_resolution",
            "amount",
            "currency",
            "metadata",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "requested_resolution": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, case=None, **kwargs):
        super().__init__(*args, **kwargs)
        if case is not None:
            self.fields["claimant"].queryset = case.parties.select_related("person").all()


class TribunalEvidenceForm(forms.ModelForm):
    class Meta:
        model = TribunalEvidence
        fields = [
            "submitted_by",
            "evidence_type",
            "title",
            "description",
            "file",
            "file_hash",
            "external_reference",
            "external_url",
            "related_object",
            "metadata",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, case=None, **kwargs):
        super().__init__(*args, **kwargs)
        if case is not None:
            self.fields["submitted_by"].queryset = case.parties.select_related("person").all()


class TribunalRulingForm(forms.ModelForm):
    class Meta:
        model = TribunalRuling
        fields = [
            "decided_by",
            "title",
            "decision",
            "reasoning",
            "resolution_type",
            "amount_awarded",
            "currency",
            "fine_debtor_account",
            "fine_creditor_account",
            "fine_asset",
            "fine_amount",
            "fine_due_at",
            "effective_at",
            "metadata",
        ]
        widgets = {
            "decision": forms.Textarea(attrs={"rows": 5}),
            "reasoning": forms.Textarea(attrs={"rows": 4}),
            "fine_due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "effective_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, case=None, **kwargs):
        super().__init__(*args, **kwargs)
        order = getattr(case, "related_order", None)
        if order:
            self.fields["fine_debtor_account"].initial = order.ledger_account_id
            self.fields["fine_creditor_account"].initial = getattr(order.shop, "ledger_account_id", None)
            self.fields["fine_asset"].initial = order.ledger_asset_id
            self.fields["fine_amount"].initial = order.total_amount

class JurySessionForm(forms.ModelForm):
    closes_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        label='Closes date',
    )
    closes_time = forms.TimeField(
        widget=forms.TimeInput(attrs={'type': 'time'}),
        label='Closes time',
        initial='23:59',
    )

    class Meta:
        model = JurySession
        fields = ['jurors', 'required_votes', 'note']
        widgets = {
            'jurors': forms.CheckboxSelectMultiple(),
            'note': forms.Textarea(attrs={'rows': 3}),
        }

    def clean(self):
        cleaned = super().clean()
        d = cleaned.get('closes_date')
        t = cleaned.get('closes_time')
        if d and t:
            import datetime
            from django.utils import timezone as tz
            naive = datetime.datetime.combine(d, t)
            cleaned['closes_at'] = tz.make_aware(naive)
        elif not self.errors:
            raise forms.ValidationError('Closing date and time are required.')
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.closes_at = self.cleaned_data['closes_at']
        if commit:
            instance.save()
        return instance


class JuryVoteForm(forms.ModelForm):
    class Meta:
        model = JuryVote
        fields = ['vote', 'reason']
        widgets = {
            'reason': forms.Textarea(attrs={'rows': 3}),
        }
