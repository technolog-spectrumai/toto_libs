import uuid
from django.core import serializers
from django.db import models
from toto.executor import RestrictedPythonExecutor


# =====================================================================
#  Django Models
# =====================================================================

class BaseExecutableModel(models.Model):
    """
    Abstract base model for any node that executes restricted Python code.
    Provides `code` and `test_context` fields plus an `execute` method.
    Delegates allowed_globals construction to child classes.
    """

    code = models.TextField(help_text="Restricted Python code snippet")
    test_context = models.JSONField(blank=True, null=True, help_text="Optional JSON context for testing")

    class Meta:
        abstract = True

    # def get_context(self, context: dict = None) -> dict:
    #     return context or self.test_context or {}
    #
    # def get_allowed_globals(self, context: dict) -> dict:
    #     """
    #     Subclasses must override this to provide their own allowed globals.
    #     """
    #     raise NotImplementedError("Child class must implement get_allowed_globals()")
    #
    # def execute(self, context: dict = None):
    #     """
    #     Execute restricted Python code using the helper executor.
    #     """
    #     ctx = self.get_context(context)
    #
    #     executor = RestrictedPythonExecutor(
    #         code=self.code,
    #         context=ctx,
    #         name=getattr(self, "name", "unnamed")
    #     )
    #
    #     # Merge model-level allowed globals with executor defaults
    #     extra_globals = self.get_allowed_globals(ctx)
    #
    #     return executor.execute(extra_globals=extra_globals)



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


