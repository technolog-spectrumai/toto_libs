from django.db import models
from django.contrib.auth.models import User
from django.utils.timezone import now

# 💱 Currency
class Currency(models.Model):
    symbol = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    is_crypto = models.BooleanField(default=True)
    decimals = models.PositiveIntegerField(default=8)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.symbol})"

# 🏛️ Chamber
class Chamber(models.Model):
    name = models.CharField(max_length=255)
    manifest = models.CharField(max_length=1024, default="")
    strategy = models.CharField(max_length=1024, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)
    default_currency = models.ForeignKey(
        Currency,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='default_for_chambers'
    )
    total_stock_emitted = models.DecimalField(max_digits=20, decimal_places=2, default=0)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

# 👤 Investor
class Investor(models.Model):
    display_name = models.CharField(max_length=100)
    manifest = models.JSONField(default=dict, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    chamber = models.ForeignKey(Chamber, on_delete=models.CASCADE, related_name='investors')
    stock_owned = models.DecimalField(max_digits=20, decimal_places=2, default=0)

    def __str__(self):
        return self.display_name

# 📦 Venture (formerly Asset)
class Venture(models.Model):
    name = models.CharField(max_length=100)
    url = models.URLField(blank=True, null=True)
    start = models.DateTimeField(default=now)
    end = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

# 💸 Transaction
class Transaction(models.Model):
    name = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    venture = models.ForeignKey(Venture, on_delete=models.CASCADE, related_name='funding_rounds')
    timestamp = models.DateTimeField(default=now)

    def __str__(self):
        return f"{self.name}: {self.amount} {self.currency.symbol} → {self.venture.name}"
