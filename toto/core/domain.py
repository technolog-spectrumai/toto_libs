import uuid
from django.db import models


class DomainEntity(models.Model):
    """
    Base class for all SQL domain models.
    Provides a universal UID for graph projection and cross‑system identity.
    """
    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        abstract = True