from django.contrib.auth.models import User
from django.db import models

class Company(models.Model):
    name = models.CharField(max_length=255, unique=True)
    registration_number = models.CharField(max_length=100, unique=True)
    country = models.CharField(max_length=100)
    industry = models.CharField(max_length=100)
    date_founded = models.DateField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class Shareholder(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='shareholders')
    full_name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    shares_owned = models.PositiveIntegerField()
    date_joined = models.DateField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='shareholder_profile')

    def __str__(self):
        return f"{self.full_name} ({self.shares_owned} shares in {self.company.name})"

class ShareTransaction(models.Model):
    shareholder = models.ForeignKey(Shareholder, on_delete=models.CASCADE, related_name='transactions')
    transaction_date = models.DateTimeField(auto_now_add=True)
    shares_changed = models.IntegerField(help_text="Positive for purchase, negative for sale")
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.transaction_date} - {self.shares_changed} shares for {self.shareholder.full_name}"

    class Meta:
        ordering = ['-transaction_date']
