import uuid
from django.core import serializers
from django.db import models




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


