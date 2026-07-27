from django.conf import settings
from django.db import models


class SocialIdentity(models.Model):
    """A stable link between a social provider account and a local user.

    Matching happens on (provider, subject) — the provider-issued id never
    changes, unlike the email the person may edit at Google/Facebook.
    """

    provider = models.CharField(max_length=32)   # ProviderSpec.key ("google", ...)
    subject = models.CharField(max_length=191)   # provider-issued stable user id
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="social_identities")
    email = models.EmailField(blank=True)        # email at last sign-in (audit only)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Social Identity"
        verbose_name_plural = "Social Identities"
        constraints = [
            models.UniqueConstraint(fields=["provider", "subject"],
                                    name="social_identity_provider_subject"),
        ]

    def __str__(self):
        return f"{self.provider}:{self.subject} -> {self.user}"
