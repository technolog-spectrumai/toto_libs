from django.db import models
from django.utils.timezone import now
from django.contrib.auth.models import User
from community.models import SocialEntity  # adjust import path as needed


class Subject(models.Model):
    social_entity = models.OneToOneField(
        SocialEntity,
        on_delete=models.CASCADE,
        related_name="subject"
    )
    name = models.CharField(max_length=255)
    legal_type = models.CharField(max_length=100, blank=True, null=True)  # e.g., 'individual', 'corporation'
    identifier = models.CharField(max_length=100, blank=True, null=True)  # e.g., tax ID, registration number
    contact_info = models.TextField(blank=True, null=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=now)

    def __str__(self):
        return self.name


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
    owner = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="accounts")
    manager = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, related_name="managed_accounts")
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    balance = models.DecimalField(max_digits=20, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(default=now)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.currency.symbol})"


# 🔁 Abstract Base: Operation
class Operation(models.Model):
    name = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    source = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="%(class)s_outgoing")
    destination = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="%(class)s_incoming")
    timestamp = models.DateTimeField(default=now)

    class Meta:
        abstract = True


    def execute(self):
        # Validate currency match
        if self.source.currency != self.currency or self.destination.currency != self.currency:
            raise ValueError("Currency mismatch between operation and accounts.")

        # Validate sufficient funds
        if self.source.balance < self.amount:
            raise ValueError(f"Insufficient funds in source account '{self.source.name}'.")

        # Perform transfer
        self.source.balance -= self.amount
        self.destination.balance += self.amount

        # Save changes
        self.source.save()
        self.destination.save()


# 💸 Transaction
class Transaction(Operation):
    def __str__(self):
        return f"{self.name}: {self.amount} {self.currency.symbol} from {self.source.name} to {self.destination.name}"

    def execute(self):
        super().execute()


# 📑 Obligation
class Obligation(Operation):
    due_date = models.DateField()
    fulfilled = models.BooleanField(default=False)

    def __str__(self):
        status = "Fulfilled" if self.fulfilled else "Pending"
        return f"{self.name}: {self.amount} {self.currency.symbol} from {self.source.name} to {self.destination.name} due {self.due_date} ({status})"

    def execute(self):
        if self.fulfilled:
            raise ValueError("Obligation already fulfilled.")

        super().execute()
        self.fulfilled = True
        self.save()

