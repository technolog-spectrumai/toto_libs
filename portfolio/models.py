from django.db import models
from django.contrib.auth.models import User
from django.utils.timezone import now
from yamabiko.models import SerializableModel


# 🏛️ Chamber
class Chamber(SerializableModel):
    name = models.CharField(max_length=255)
    default_currency = models.ForeignKey(
        'Currency',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chambers'
    )
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


# 💱 Currency
class Currency(SerializableModel):
    symbol = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    is_crypto = models.BooleanField(default=True)
    decimals = models.PositiveIntegerField(default=8)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.symbol})"

# 🏢 Company
class Company(SerializableModel):
    name = models.CharField(max_length=255, unique=True)
    registration_number = models.CharField(max_length=100, unique=True)
    country = models.CharField(max_length=100)
    industry = models.CharField(max_length=100)
    date_founded = models.DateField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

# 👤 Shareholder
class Shareholder(SerializableModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='shareholders')
    full_name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    shares_owned = models.PositiveIntegerField()
    date_joined = models.DateField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='shareholder_profile')

    def __str__(self):
        return f"{self.full_name} ({self.shares_owned} shares in {self.company.name})"

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

# 💸 Transaction
class Transaction(SerializableModel):
    name = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    venture = models.ForeignKey(Venture, on_delete=models.CASCADE, related_name='funding_rounds')
    timestamp = models.DateTimeField(default=now)

    def __str__(self):
        return f"{self.name}: {self.amount} {self.currency.symbol} → {self.venture.name}"
