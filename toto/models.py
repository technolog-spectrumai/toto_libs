import uuid
from django.db import models
from django.core import serializers



class SerializableModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True

    @classmethod
    def serialize_queryset(cls, queryset, format="json"):
        return serializers.serialize(format, queryset)

    @classmethod
    def deserialize_data(cls, data, format="json"):
        return serializers.deserialize(format, data)