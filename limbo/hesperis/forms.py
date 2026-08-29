"""Forms for contributing to a bounty and for reviewing what arrives."""

from django import forms
from django.utils.translation import gettext_lazy as _

from toto.kanban.models import (
    Campaign, ConsensusPolicy, Mission, Project, ReviewVerdict, RewardPolicy,
    RewardTrigger,
)
from toto.people.models import Person

from .models import HesperisBounty, HesperisCampaign

_INPUT = ("w-full rounded-lg border px-3 py-2 text-sm shadow-sm")
_DARK = ("darkMode ? 'bg-bubble-bg-dark border-accent-1' "
         ": 'bg-bubble-bg-light border-accent-2'")


class ContributionForm(forms.Form):
    """What a contributor sends: prose, files, and where/when it was observed.

    Files are chosen from the vault rather than uploaded here. That is not a
    shortcut — it is the platform's rule: bytes stay vault-governed, so
    downloads keep going through vault's own access rules and an antivirus
    scan has already happened at the door the file arrived by. The queryset is
    ``accessible_files(user, include_public=False)``: files this person has a
    CLAIM on, not merely ones they can read, because this attaches them.
    """

    notes = forms.CharField(
        label=_("What did you observe?"),
        widget=forms.Textarea(attrs={
            "rows": 5, "class": _INPUT, "x-bind:class": _DARK,
            "placeholder": _("Describe what you found, where, and anything a reviewer should know."),
        }),
        required=False,
    )
    observed_at = forms.DateTimeField(
        label=_("Observed at"),
        required=False,
        widget=forms.DateTimeInput(attrs={
            "type": "datetime-local", "class": _INPUT, "x-bind:class": _DARK}),
    )
    files = forms.ModelMultipleChoiceField(
        label=_("Attach files from your vault"),
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from toto.vault.filetree import accessible_files

        if user is not None:
            self.fields["files"].queryset = accessible_files(
                user, include_public=False)
        else:
            from toto.vault.models import VaultFile
            self.fields["files"].queryset = VaultFile.objects.none()


class ReviewForm(forms.Form):
    """One reviewer's verdict."""

    verdict = forms.ChoiceField(
        choices=ReviewVerdict.choices,
        widget=forms.RadioSelect,
        label=_("Your verdict"),
    )
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 3, "class": _INPUT, "x-bind:class": _DARK,
            "placeholder": _("Optional: why?"),
        }),
        label=_("Comment"),
    )


# ── Staff: running a collection programme ────────────────────────────────────
#
# One form, several models. A campaign is a kanban Campaign plus its Hesperis
# extension; a bounty is a kanban Mission plus its extension plus, usually, a
# RewardPolicy. Staff should not have to know that — they are making "a
# bounty", and the form is where the engine's shape stays out of their way.


class CampaignForm(forms.Form):
    """Create a collection programme: a kanban Campaign and its extension."""

    project = forms.ModelChoiceField(
        label=_("Project"),
        queryset=Project.objects.none(),
        help_text=_("The kanban project this programme lives under."),
        widget=forms.Select(attrs={"class": _INPUT, "x-bind:class": _DARK}),
    )
    name = forms.CharField(
        label=_("Name"), max_length=200,
        widget=forms.TextInput(attrs={
            "class": _INPUT, "x-bind:class": _DARK,
            "placeholder": _("e.g. Bridges of the Vistula")}),
    )
    description = forms.CharField(
        label=_("Description"), required=False,
        widget=forms.Textarea(attrs={
            "rows": 3, "class": _INPUT, "x-bind:class": _DARK}),
    )
    licence = forms.CharField(
        label=_("Licence"), max_length=100, required=False,
        widget=forms.TextInput(attrs={
            "class": _INPUT, "x-bind:class": _DARK,
            "placeholder": "CC BY-SA 4.0"}),
    )
    consensus_policy = forms.ModelChoiceField(
        label=_("Default review rule"),
        queryset=ConsensusPolicy.objects.all(),
        required=False,
        help_text=_("Bounties inherit this unless they set their own."),
        widget=forms.Select(attrs={"class": _INPUT, "x-bind:class": _DARK}),
    )
    is_open = forms.BooleanField(
        label=_("Accepting contributions"), required=False, initial=True)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Staff see every project — they are running the programme, and the
        # kanban membership rule is about who WORKS on a project, not who
        # may open a collection drive under it.
        self.fields["project"].queryset = Project.objects.order_by("name")

    def save(self, *, owner: Person | None):
        data = self.cleaned_data
        campaign = Campaign.objects.create(
            project=data["project"],
            name=data["name"],
            description=data["description"],
            owner=owner,
            consensus_policy=data["consensus_policy"],
        )
        HesperisCampaign.objects.create(
            campaign=campaign,
            licence=data["licence"],
            is_open=data["is_open"],
        )
        return campaign


class BountyForm(forms.Form):
    """Create or edit a bounty: a Mission, its extension, and its reward."""

    campaign = forms.ModelChoiceField(
        label=_("Campaign"),
        queryset=Campaign.objects.none(),
        widget=forms.Select(attrs={"class": _INPUT, "x-bind:class": _DARK}),
    )
    title = forms.CharField(
        label=_("Title"), max_length=200,
        widget=forms.TextInput(attrs={
            "class": _INPUT, "x-bind:class": _DARK,
            "placeholder": _("e.g. Photograph the bridges")}),
    )
    instructions = forms.CharField(
        label=_("Instructions"), required=False,
        help_text=_("What to collect, and what counts as good."),
        widget=forms.Textarea(attrs={
            "rows": 5, "class": _INPUT, "x-bind:class": _DARK}),
    )
    consensus_policy = forms.ModelChoiceField(
        label=_("Review rule"),
        queryset=ConsensusPolicy.objects.all(),
        required=False,
        help_text=_("Blank inherits the campaign's. No rule anywhere means no review gate."),
        widget=forms.Select(attrs={"class": _INPUT, "x-bind:class": _DARK}),
    )
    reward_amount = forms.IntegerField(
        label=_("Reward per accepted observation"),
        required=False, min_value=0,
        help_text=_("In base units of the asset below. 0 or blank means no reward."),
        widget=forms.NumberInput(attrs={"class": _INPUT, "x-bind:class": _DARK}),
    )
    reward_asset = forms.ModelChoiceField(
        label=_("Reward asset"),
        queryset=None,
        required=False,
        help_text=_("A real ledger asset. Nothing here means nothing can be paid yet."),
        widget=forms.Select(attrs={"class": _INPUT, "x-bind:class": _DARK}),
    )
    funding_account = forms.CharField(
        label=_("Funding account code"), max_length=100, required=False,
        help_text=_("The ledger account that pays. Left blank, the campaign treasury is used."),
        widget=forms.TextInput(attrs={"class": _INPUT, "x-bind:class": _DARK}),
    )
    max_contributions = forms.IntegerField(
        label=_("Maximum contributions"), required=False, min_value=1,
        widget=forms.NumberInput(attrs={"class": _INPUT, "x-bind:class": _DARK}),
    )
    opens_at = forms.DateTimeField(
        label=_("Opens"), required=False,
        widget=forms.DateTimeInput(attrs={
            "type": "datetime-local", "class": _INPUT, "x-bind:class": _DARK}),
    )
    closes_at = forms.DateTimeField(
        label=_("Closes"), required=False,
        widget=forms.DateTimeInput(attrs={
            "type": "datetime-local", "class": _INPUT, "x-bind:class": _DARK}),
    )

    def __init__(self, *args, instance: HesperisBounty | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        # Only campaigns that ARE collection programmes: a bounty under a
        # plain kanban campaign would have no Hesperis treasury to fund it.
        self.fields["campaign"].queryset = (
            Campaign.objects.filter(hesperis__isnull=False).order_by("name"))
        # The ledger's live assets. Imported here, not at module scope — this
        # app only runs where toto.assets is installed (its AppConfig checks),
        # but forms.py is imported by views.py and must not be the thing that
        # breaks `manage.py check` on a misconfigured host.
        from toto.assets.models import Asset  # noqa: PLC0415
        self.fields["reward_asset"].queryset = (
            Asset.objects.filter(active=True).order_by("unit_name"))
        self.fields["reward_asset"].label_from_instance = (
            lambda a: f"{a.unit_name} — {a.name}" if a.name else a.unit_name)
        if instance is not None:
            self.fields["campaign"].disabled = True
            self.fields["campaign"].required = False
            self._seed_from(instance)

    def _seed_from(self, bounty):
        mission = bounty.mission
        reward = _collection_reward_of(bounty)
        self.initial.update({
            "campaign": mission.campaign_id,
            "title": mission.title,
            "instructions": bounty.instructions,
            "consensus_policy": mission.consensus_policy_id,
            "reward_amount": reward.amount_base_units if reward else None,
            "reward_asset": self._asset_for_code(reward.asset_code) if reward else None,
            "funding_account": reward.funding_account_code if reward else "",
            "max_contributions": bounty.max_contributions,
            "opens_at": bounty.opens_at,
            "closes_at": bounty.closes_at,
        })

    @staticmethod
    def _asset_for_code(code):
        """The Asset a stored symbol names, by the same lookup the backend uses."""
        from toto.assets.models import Asset  # noqa: PLC0415
        return (Asset.objects.filter(unit_name=code).first()
                or Asset.objects.filter(code=code).first())

    def clean(self):
        data = super().clean()
        opens, closes = data.get("opens_at"), data.get("closes_at")
        if opens and closes and closes <= opens:
            self.add_error("closes_at", _("Must be after it opens."))
        if data.get("reward_amount") and data.get("reward_asset") is None:
            self.add_error("reward_asset", _("Pick the asset the reward is paid in."))
        return data

    def save(self, *, owner: Person | None) -> HesperisBounty:
        data = self.cleaned_data
        if self.instance is None:
            campaign = data["campaign"]
            mission = Mission.objects.create(
                campaign=campaign,
                title=data["title"],
                description=data["instructions"][:500],
                owner=owner,
                consensus_policy=data["consensus_policy"],
            )
            bounty = HesperisBounty.objects.create(mission=mission)
        else:
            bounty = self.instance
            mission = bounty.mission
            mission.title = data["title"]
            mission.consensus_policy = data["consensus_policy"]
            mission.save(update_fields=["title", "consensus_policy"])

        bounty.instructions = data["instructions"]
        bounty.max_contributions = data["max_contributions"]
        bounty.opens_at = data["opens_at"]
        bounty.closes_at = data["closes_at"]
        bounty.save()

        self._save_reward(bounty, data)
        return bounty

    @staticmethod
    def _save_reward(bounty, data):
        """One SUBMISSION_ACCEPTED policy per bounty, created/updated/removed.

        Mission-scoped so it overrides any campaign-wide policy. A blank or
        zero amount removes the bounty's own policy, which makes the campaign's
        (if any) apply again — the same precedence the board reads.
        """
        amount = data.get("reward_amount") or 0
        existing = RewardPolicy.objects.filter(
            mission=bounty.mission,
            trigger=RewardTrigger.SUBMISSION_ACCEPTED).first()
        if amount <= 0:
            if existing is not None:
                existing.delete()
            return
        funding = (data.get("funding_account") or "").strip()
        if not funding:
            pc = bounty.campaign
            funding = pc.treasury_account_code if pc is not None else ""
        fields = {
            # unit_name, because that is what AssetsRewardBackend resolves
            # first. Storing the pk would put a kanban row in the position of
            # knowing what a ledger asset is — the symbol keeps it a string.
            "asset_code": data["reward_asset"].unit_name,
            "amount_base_units": amount,
            "funding_account_code": funding,
            "active": True,
        }
        if existing is None:
            RewardPolicy.objects.create(
                mission=bounty.mission,
                trigger=RewardTrigger.SUBMISSION_ACCEPTED, **fields)
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.save()


def _collection_reward_of(bounty):
    return RewardPolicy.objects.filter(
        mission=bounty.mission,
        trigger=RewardTrigger.SUBMISSION_ACCEPTED).first()

