import uuid
from django.core import serializers
from datetime import timedelta
from django.db import models
from RestrictedPython import compile_restricted, safe_builtins, utility_builtins, limited_builtins
import operator
from RestrictedPython.Guards import guarded_unpack_sequence, full_write_guard


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

    def get_context(self, context: dict = None) -> dict:
        """
        Default context resolution. Subclasses can override if needed.
        """
        return context or self.test_context or {}

    def get_allowed_globals(self, context: dict) -> dict:
        """
        Subclasses must override this to provide their own allowed globals.
        """
        raise NotImplementedError("Child class must implement get_allowed_globals()")

    def execute(self, context: dict = None):
        """
        Execute restricted Python code. Requires a main(context) function.
        """
        ctx = self.get_context(context)

        try:
            byte_code = compile_restricted(
                self.code,
                filename=f"<lambda:{getattr(self, 'name', 'unnamed')}>",
                mode="exec"
            )

            allowed_globals = self.get_allowed_globals(ctx)
            local_vars = {}
            exec(byte_code, allowed_globals, local_vars)

            if "main" not in local_vars or not callable(local_vars["main"]):
                return {"error": "No main() function defined in node code"}

            return local_vars["main"](ctx)

        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def get_default_allowed_globals() -> dict:
        allowed_builtins = {}
        allowed_builtins.update(safe_builtins)
        allowed_builtins.update(utility_builtins)
        allowed_builtins.update(limited_builtins)

        return {
            "__builtins__": allowed_builtins,
            "_getitem_": operator.getitem,
            "_setitem_": operator.setitem,
            "_delitem_": operator.delitem,
            "_unpack_sequence_": guarded_unpack_sequence,
            "_getiter_": iter,
            "timedelta": timedelta,
            "sum": sum,
            "len": len,
            "max": max,
            "min": min,
            "_write_": full_write_guard,
        }



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


