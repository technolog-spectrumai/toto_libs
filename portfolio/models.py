from django.db import models
from django.contrib.auth.models import User


class GeneralPerson(models.Model):
    display_name = models.CharField(max_length=100)
    wallet_address = models.CharField(max_length=255, blank=True, null=True)
    manifest = models.JSONField(default=dict, blank=True)  # Strategy or role manifest
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self.display_name

# 👤 Investor (extends GeneralPerson)
class Investor(GeneralPerson):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    balance = models.DecimalField(max_digits=20, decimal_places=2, default=0)  # Internal currency or crypto
    kyc_verified = models.BooleanField(default=False)

# 🧑‍🤝‍🧑 Associate (extends GeneralPerson, no User link)
class Associate(GeneralPerson):
    role = models.CharField(max_length=100, blank=True)  # e.g., 'Advisor', 'Partner', 'Mentor'
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.display_name

# 💼 Portfolio
class Portfolio(models.Model):
    investor = models.ForeignKey(Investor, on_delete=models.CASCADE, related_name='portfolios')
    name = models.CharField(max_length=100)
    strategy = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.investor.display_name})"

# 📦 Asset
class Asset(models.Model):
    portfolio = models.ForeignKey(Portfolio, on_delete=models.CASCADE, related_name='assets')
    name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=20)
    quantity = models.DecimalField(max_digits=20, decimal_places=8)

    def __str__(self):
        return f"{self.symbol} - {self.quantity}"

# 💱 Currency
class Currency(models.Model):
    symbol = models.CharField(max_length=20, unique=True)  # e.g., BTC, ETH, USD, EFFORT
    name = models.CharField(max_length=100)
    is_crypto = models.BooleanField(default=True)
    is_internal = models.BooleanField(default=False)  # True for EFFORT or platform-native tokens
    decimals = models.PositiveIntegerField(default=8)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.symbol})"

# 🔄 Currency Exchange Rate
class CurrencyExchangeRate(models.Model):
    from_currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name='exchange_from')
    to_currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name='exchange_to')
    rate = models.DecimalField(max_digits=20, decimal_places=8)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('from_currency', 'to_currency')

    def __str__(self):
        return f"1 {self.from_currency.symbol} = {self.rate} {self.to_currency.symbol}"

# 🔁 Transaction
class Transaction(models.Model):
    investor = models.ForeignKey(Investor, on_delete=models.CASCADE, related_name='transactions')
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    reason = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.amount} {self.currency.symbol} for {self.investor.display_name}"


class GeneralEvent(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self


class Milestone(GeneralEvent):
    portfolio = models.ForeignKey('Portfolio', on_delete=models.CASCADE, related_name='milestones')
    category = models.CharField(max_length=100)  # e.g., 'Funding', 'Launch', 'Partnership'
    achieved = models.BooleanField(default=False)
    target_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"Milestone: {self.title} for {self.portfolio.name}"


class Event(GeneralEvent):
    owner = models.ForeignKey('Investor', on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=100)  # e.g., 'Transaction', 'Login', 'Strategy Update'
    severity = models.CharField(
        max_length=50,
        choices=[('INFO', 'Info'), ('WARNING', 'Warning'), ('CRITICAL', 'Critical')],
        default='INFO'
    )
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"Event: {self.title} ({self.event_type}) by {self.owner.display_name}"

