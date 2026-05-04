import uuid
from django.db import models
from neomodel import (
    StructuredNode, StringProperty
)


class DomainEntity(models.Model):
    """
    Base class for all SQL domain models.
    Provides a universal UID for graph projection and cross‑system identity.
    """
    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        abstract = True


class DomainNode(StructuredNode):
    __abstract_node__ = True
    uuid = StringProperty(
        unique_index=True,
        required=True
    )