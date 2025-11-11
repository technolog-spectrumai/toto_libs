from django.db import models
from django.contrib.auth.models import User
from yamabiko.models import SerializableModel
from community.models import SocialEntity
from finance.models import Transaction, Currency
from django.utils.timezone import now
from django.db import models
from yamabiko.models import SerializableModel
from community.models import SocialEntity, CommunityMember
from django.utils.timezone import now

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
class Company(SerializableModel):
    name = models.CharField(max_length=255, unique=True)
    registration_number = models.CharField(max_length=100, unique=True)
    country = models.CharField(max_length=100)
    industry = models.CharField(max_length=100)
    date_founded = models.DateField()
    is_active = models.BooleanField(default=True)
    social_entity = models.OneToOneField(
        SocialEntity,
        on_delete=models.CASCADE,
        related_name="company",
        null=True,
        blank=True
    )


# 👤 Shareholder
class Shareholder(SerializableModel):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='shareholders'
    )
    full_name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    shares_owned = models.PositiveIntegerField()
    date_joined = models.DateField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    # Link to SocialEntity
    social_entity = models.OneToOneField(
        SocialEntity,
        on_delete=models.CASCADE,
        related_name="shareholder",
        null=True,
        blank=True
    )

    def __str__(self):
        return f"{self.full_name} ({self.shares_owned} shares in {self.company.name})"

    # -----------------------------
    # Identity helpers
    # -----------------------------
    def get_identity_type(self):
        """
        Infer identity type from linked SocialEntity.
        """
        if self.social_entity:
            return self.social_entity.get_identity_type()
        return "Shareholder"

    def get_user(self):
        """
        Return linked User if this Shareholder is tied to a CommunityMember.
        """
        if self.social_entity and hasattr(self.social_entity, "member"):
            return self.social_entity.member.user
        return None

    def get_company(self):
        """
        Return linked Company if this Shareholder is tied to a Company.
        """
        if self.social_entity and hasattr(self.social_entity, "company"):
            return self.social_entity.company
        return None

    def get_member(self):
        """
        Return linked CommunityMember if this Shareholder is tied to a CommunityMember.
        """
        if self.social_entity and hasattr(self.social_entity, "member"):
            return self.social_entity.member
        return None


# 📦 Venture
class Venture(SerializableModel):
    name = models.CharField(max_length=100)
    url = models.URLField(blank=True, null=True)
    start = models.DateTimeField(default=now)
    end = models.DateTimeField(null=True, blank=True)
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ventures'
    )

    def __str__(self):
        return self.name


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
