from django.db import models
from django.utils.timezone import now
from django.contrib.auth.models import User
from federal.models import IdentityProfile


# 💱 Currency
class Currency(models.Model):
    symbol = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    is_crypto = models.BooleanField(default=True)
    decimals = models.PositiveIntegerField(default=8)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.symbol})"


# 🏦 Account
class Account(models.Model):
    name = models.CharField(max_length=255, unique=True)

    owner = models.ForeignKey(
        IdentityProfile,
        on_delete=models.CASCADE,
        related_name="accounts",
        db_comment="owned_by"
    )

    manager = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="managed_accounts",
        db_comment="managed_by"
    )

    currency = models.ForeignKey(
        Currency,
        on_delete=models.CASCADE,
        db_comment="denominated_in"
    )

    balance = models.DecimalField(max_digits=20, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(default=now)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.currency.symbol})"


# 💸 Transaction
class Transaction(models.Model):
    name = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=20, decimal_places=2)

    source = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="%(class)s_outgoing",
        db_comment="source_account"
    )
    destination = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="%(class)s_incoming",
        db_comment="destination_account"
    )

    conversion_rate = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=1.0,
        help_text="Rate from source currency to destination currency."
    )

    timestamp = models.DateTimeField(default=now)

    def __str__(self):
        return (
            f"{self.name}: {self.amount} {self.source.currency.symbol} "
            f"from {self.source.name} to {self.destination.name}"
        )

    def execute(self):
        if self.source.balance < self.amount:
            raise ValueError(
                f"Insufficient funds in source account '{self.source.name}'."
            )

        if self.source.currency == self.destination.currency:
            converted_amount = self.amount
        else:
            if self.conversion_rate <= 0:
                raise ValueError("Conversion rate must be greater than zero.")
            converted_amount = self.amount * self.conversion_rate

        self.source.balance -= self.amount
        self.destination.balance += converted_amount

        self.source.save()
        self.destination.save()
