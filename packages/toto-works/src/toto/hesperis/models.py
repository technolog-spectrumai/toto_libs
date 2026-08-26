"""Hesperis: bounty semantics, accepted observations, versioned datasets.

Everything about *doing the work* belongs to ``toto.kanban`` and is reused
here, not re-implemented:

    kanban.Campaign   → HesperisCampaign   (a collection programme)
    kanban.Mission    → HesperisBounty     (a call for contributions)
    kanban.Task       → one contributor's slot under a bounty
    kanban.Submission → what somebody claimed
    kanban.Review     → what a reviewer decided
    consensus         → whether it was accepted
    RewardPolicy      → the gems, if any

What Hesperis adds is the part kanban has no opinion about: the difference
between a claim and a fact, and the difference between a live query and a
release.

Unlike kanban, this app MAY hold foreign keys into ``toto.assets``. It is
installed only where the ledger is (its AppConfig refuses to start otherwise),
so gems can be a real Asset and a real LedgerAccount rather than a string that
something else has to resolve.
"""

import hashlib

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from toto.core.domain import DomainEntity
from toto.people.models import Person


class HesperisCampaign(DomainEntity):
    """A collection programme: one kanban Campaign, read as data gathering.

    An EXTENSION, not a copy. Dates, owner, project and zone stay on the
    kanban row; only what kanban has no opinion about lives here.
    """

    campaign = models.OneToOneField(
        "kanban.Campaign", on_delete=models.CASCADE, related_name="hesperis")
    #: What an observation must carry, as a JSON description. Data, not code:
    #: a campaign changes what it collects far more often than this ships.
    collection_scheme = models.JSONField(blank=True, null=True)
    licence = models.CharField(max_length=100, blank=True)
    attribution = models.CharField(max_length=200, blank=True)
    is_open = models.BooleanField(
        default=True, help_text="Whether new contributions are accepted at all.")

    # ── the gem purse ────────────────────────────────────────────────────────
    # Per campaign, so one programme cannot spend another's budget and an
    # exhausted purse fails that campaign's settlements alone. A real FK, not a
    # code: this app is only ever installed where the ledger is.
    gem_asset = models.ForeignKey(
        "assets.Asset", on_delete=models.PROTECT, null=True, blank=True,
        related_name="hesperis_campaigns",
        help_text="The decorative asset rewards are paid in.")
    treasury_account = models.ForeignKey(
        "assets.LedgerAccount", on_delete=models.PROTECT, null=True, blank=True,
        related_name="hesperis_campaigns",
        help_text="This campaign's gem purse. Empty means rewards fail, not that work stops.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Hesperis campaign"

    def __str__(self):
        return self.campaign.name

    @property
    def treasury_account_code(self):
        """What a kanban RewardPolicy stores in ``funding_account_code``.

        The seam: kanban holds an opaque string and never learns the words
        "campaign treasury"; Hesperis is what puts a meaningful one there.
        """
        return self.treasury_account.code if self.treasury_account_id else ""


class HesperisBounty(DomainEntity):
    """A mission, read as a call for contributions.

    A bounty is not a task. It is an open invitation that many people answer,
    and each answer becomes its OWN kanban Task under this bounty's mission
    (see ``kanban.work.open_assignment``). That is what lets one bounty take a
    hundred contributions without a second task system.
    """

    mission = models.OneToOneField(
        "kanban.Mission", on_delete=models.CASCADE, related_name="hesperis_bounty")
    instructions = models.TextField(
        blank=True, help_text="What to collect, and what counts as good.")
    reward_summary = models.CharField(
        max_length=200, blank=True,
        help_text="Display only. The actual reward is a kanban RewardPolicy.")
    collection_settings = models.JSONField(blank=True, null=True)
    max_contributions = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Blank means unlimited.")
    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Hesperis bounties"
        ordering = ("-created_at", "-pk")

    def __str__(self):
        return self.mission.title

    @property
    def campaign(self):
        return getattr(self.mission.campaign, "hesperis", None)

    def is_open(self, *, now=None) -> bool:
        """Whether this bounty takes contributions right now.

        Four independent reasons to be shut, all checked here so the board, the
        contribute view and the API cannot each remember three of them.
        """
        now = now or timezone.now()
        campaign = self.campaign
        if campaign is not None and not campaign.is_open:
            return False
        if self.opens_at and now < self.opens_at:
            return False
        if self.closes_at and now > self.closes_at:
            return False
        if self.max_contributions is not None:
            if self.submission_count() >= self.max_contributions:
                return False
        return True

    def submission_count(self) -> int:
        from toto.kanban.models import Submission, SubmissionState
        return Submission.objects.filter(
            task__mission_id=self.mission_id,
        ).exclude(state=SubmissionState.DRAFT).count()

    def accepted_count(self) -> int:
        return AcceptedObservation.objects.filter(bounty=self).count()

    def awaiting_review_count(self) -> int:
        from toto.kanban.models import (
            Submission, SubmissionResolution, SubmissionState)
        return Submission.objects.filter(
            task__mission_id=self.mission_id,
            state=SubmissionState.SUBMITTED,
            resolution=SubmissionResolution.PENDING,
        ).count()


class AcceptedObservation(DomainEntity):
    """A claim that survived review — the durable fact.

    Deliberately a DIFFERENT TABLE from Submission, and the raw row is never
    edited into this one. A submission is what a person said; this is what the
    reviewers let through, and conflating them is how a dataset quietly becomes
    "whatever was submitted".

    The OneToOne is the structural half of the guarantee: one accepted
    submission yields at most one observation, enforced by the database rather
    than by whoever remembers. The other half is that
    ``services.accept`` is the only writer, and it refuses anything whose
    resolution did not come from consensus.
    """

    submission = models.OneToOneField(
        "kanban.Submission",
        # PROTECT, not CASCADE: an accepted observation is provenance. Deleting
        # the submission it came from must fail loudly rather than silently
        # removing the evidence for a row in a published dataset.
        on_delete=models.PROTECT,
        related_name="accepted_observation",
    )
    bounty = models.ForeignKey(
        HesperisBounty, on_delete=models.PROTECT, related_name="observations")
    observed_by = models.ForeignKey(
        Person, on_delete=models.PROTECT, related_name="hesperis_observations")
    observed_at = models.DateTimeField(null=True, blank=True)
    location = models.ForeignKey(
        "locations.Address", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="hesperis_observations")
    zone = models.ForeignKey(
        "locations.Zone", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="hesperis_observations")
    payload = models.JSONField(blank=True, null=True)
    accepted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-accepted_at", "-pk")

    def __str__(self):
        return f"Observation #{self.pk} ({self.bounty})"


class Dataset(DomainEntity):
    """A named collection of accepted observations, under one campaign."""

    campaign = models.ForeignKey(
        HesperisCampaign, on_delete=models.CASCADE, related_name="datasets")
    name = models.CharField(max_length=200)
    slug = models.SlugField()
    description = models.TextField(blank=True)
    licence = models.CharField(max_length=100, blank=True)
    #: Where this dataset's own files live. A LINK, not a copy: Hesperis owns no
    #: file storage, so a release that ships video or imagery keeps the bytes in
    #: the vault, under the vault's quota, encryption, antivirus and sharing —
    #: and this app stores only which bucket that is.
    #:
    #: SET_NULL rather than PROTECT: losing the bucket must not make the dataset
    #: and its frozen releases undeletable. The releases are the record; the
    #: bucket is where the heavy bytes happen to sit.
    bucket = models.ForeignKey(
        "vault.Bucket", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="hesperis_datasets",
        help_text="The vault bucket holding this dataset's files.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "slug"], name="hesperis_dataset_slug_per_campaign"),
        ]

    def __str__(self):
        return self.name

    def live_observation_count(self) -> int:
        """What a NEW version would freeze. Not what any existing one holds."""
        return AcceptedObservation.objects.filter(
            bounty__mission__campaign_id=self.campaign.campaign_id).count()


class DatasetVersion(DomainEntity):
    """An immutable RELEASE. Not a live query.

    Membership is copied into rows at freeze time and never recomputed, so a
    version published in March still means in December exactly what it meant in
    March — even though the campaign has accepted a thousand observations
    since. ``manifest_hash`` is what makes that checkable rather than merely
    asserted: recompute it and compare.
    """

    dataset = models.ForeignKey(
        Dataset, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    notes = models.TextField(blank=True)
    manifest_hash = models.CharField(max_length=64, blank=True)
    frozen_at = models.DateTimeField(default=timezone.now)
    frozen_by = models.ForeignKey(
        Person, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="hesperis_frozen_versions")

    class Meta:
        ordering = ("-number",)
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "number"], name="hesperis_version_number_per_dataset"),
        ]

    def __str__(self):
        return f"{self.dataset.name} v{self.number}"

    def compute_manifest_hash(self) -> str:
        """SHA-256 over the ordered member uids.

        Over UIDs rather than primary keys: a uid survives a restore into a
        different database, and a release that could not be verified after a
        restore would be a release in name only.
        """
        uids = (self.members
                .order_by("observation__uid")
                .values_list("observation__uid", flat=True))
        digest = hashlib.sha256()
        for uid in uids:
            digest.update(str(uid).encode())
            digest.update(b"\n")
        return digest.hexdigest()

    def verify(self) -> bool:
        """Is this release still exactly what was frozen?"""
        return bool(self.manifest_hash) and \
            self.manifest_hash == self.compute_manifest_hash()


class DatasetVersionMember(DomainEntity):
    """One observation's membership of one frozen release."""

    version = models.ForeignKey(
        DatasetVersion, on_delete=models.CASCADE, related_name="members")
    observation = models.ForeignKey(
        AcceptedObservation,
        # PROTECT: a published release must not lose rows because somebody
        # tidied up the observation table.
        on_delete=models.PROTECT,
        related_name="version_memberships",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["version", "observation"],
                name="hesperis_unique_version_member"),
        ]

    def __str__(self):
        return f"{self.observation} in {self.version}"

    def save(self, *args, **kwargs):
        """Membership is written at freeze time and never afterwards.

        A frozen version that could gain a row later would be a live query
        wearing a release's clothes, which is the one thing DatasetVersion
        exists not to be.
        """
        if self._state.adding and self.version_id:
            if self.version.manifest_hash:
                raise ValidationError(
                    _("This dataset version is frozen; publish a new version "
                      "instead of adding to it."))
        elif not self._state.adding:
            raise ValidationError(
                _("Dataset membership cannot be edited."))
        super().save(*args, **kwargs)
