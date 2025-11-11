from django.db import models
from django.utils.timezone import now
from django.contrib.auth.models import User
from community.models import SocialEntity  # adjust import path as needed
from yamabiko.models import SerializableModel


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


class ExchangeRate(models.Model):
    base_currency = models.ForeignKey(
        Currency,
        on_delete=models.CASCADE,
        related_name="base_rates"
    )
    quote_currency = models.ForeignKey(
        Currency,
        on_delete=models.CASCADE,
        related_name="quote_rates"
    )
    rate = models.DecimalField(max_digits=20, decimal_places=8)
    timestamp = models.DateTimeField(default=now)

    class Meta:
        unique_together = ("base_currency", "quote_currency", "timestamp")
        ordering = ["-timestamp"]

    def __str__(self):
        return f"1 {self.base_currency.symbol} = {self.rate} {self.quote_currency.symbol} @ {self.timestamp:%Y-%m-%d %H:%M}"



# 🏦 Account
class Account(SerializableModel):
    name = models.CharField(max_length=255, unique=True)
    owner = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="accounts")
    manager = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, related_name="managed_accounts")
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    balance = models.DecimalField(max_digits=20, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(default=now)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.currency.symbol})"


class Transaction(SerializableModel):
    name = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    source = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="%(class)s_outgoing")
    destination = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="%(class)s_incoming")
    timestamp = models.DateTimeField(default=now)

    def __str__(self):
        return f"{self.name}: {self.amount} {self.currency.symbol} from {self.source.name} to {self.destination.name}"

    def execute(self):
        # Validate source currency match
        if self.source.currency != self.currency:
            raise ValueError("Source account currency does not match operation currency.")

        # Validate sufficient funds
        if self.source.balance < self.amount:
            raise ValueError(f"Insufficient funds in source account '{self.source.name}'.")

        # Convert amount if destination uses a different currency
        if self.destination.currency == self.currency:
            converted_amount = self.amount
        else:
            try:
                rate = ExchangeRate.objects.filter(
                    base_currency=self.currency,
                    quote_currency=self.destination.currency
                ).latest("timestamp")
            except ExchangeRate.DoesNotExist:
                raise ValueError(f"No exchange rate from {self.currency.symbol} to {self.destination.currency.symbol}.")

            converted_amount = self.amount * rate.rate

        # Perform transfer
        self.source.balance -= self.amount
        self.destination.balance += converted_amount

        # Save changes
        self.source.save()
        self.destination.save()


