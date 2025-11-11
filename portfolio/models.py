from community.models import SocialEntity
from finance.models import Transaction, Currency
from django.db import models
from toto.models import SerializableModel
from community.models import SocialEntity, CommunityMember, Community
from django.utils.timezone import now
from ravioli.models import Subject

# 🏛️ Chamber
class Chamber(SerializableModel):
    name = models.CharField(max_length=255)
    default_currency = models.ForeignKey(
        Currency,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chambers'
    )
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


# 🏢 Company
class Company(SocialEntity):
    name = models.CharField(max_length=255, unique=True)
    registration_number = models.CharField(max_length=100, unique=True)
    country = models.CharField(max_length=100)
    industry = models.CharField(max_length=100)
    date_founded = models.DateField()
    is_active = models.BooleanField(default=True)


class SharePackage(Subject):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='share_packages'
    )
    shares_owned = models.PositiveIntegerField()
    date_joined = models.DateField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    # Compulsory link to SocialEntity
    social_entity = models.ForeignKey(
        SocialEntity,
        on_delete=models.CASCADE,
        related_name="share_package_ownership"
    )

    def __str__(self):
        return f"{self.get_full_name()} ({self.shares_owned} shares in {self.company.name})"

    # -----------------------------
    # Identity helpers
    # -----------------------------
    def get_identity_type(self):
        return self.social_entity.get_identity_type()

    def get_user(self):
        if self.social_entity and hasattr(self.social_entity, "member"):
            return self.social_entity.member.user
        return None

    def get_company(self):
        if self.social_entity and hasattr(self.social_entity, "company"):
            return self.social_entity.company
        return None

    def get_member(self):
        if self.social_entity and hasattr(self.social_entity, "member"):
            return self.social_entity.member
        return None

    # -----------------------------
    # New convenience methods
    # -----------------------------
    def get_full_name(self):
        """
        Resolve a human-readable name from the linked SocialEntity.
        """
        if isinstance(self.social_entity, CommunityMember):
            return self.social_entity.display_name
        elif isinstance(self.social_entity, Company):
            return self.social_entity.name
        elif isinstance(self.social_entity, Community):
            return self.social_entity.name
        return f"Entity {self.social_entity.id}"


class FundingRound(SerializableModel):
    venture = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="funding_rounds"
    )
    name = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(default=now)
    transaction = models.OneToOneField(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="funding_round"
    )

    def __str__(self):
        return f"{self.name} — {self.amount} {self.currency.symbol} for {self.venture.name}"
