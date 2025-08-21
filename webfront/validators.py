import os
import json
from django.core.exceptions import ValidationError
from jsonschema import validate as jsonschema_validate, ValidationError as JSONSchemaValidationError
from django.conf import settings


class DataValidator:
    TEMPLATE_DIR = os.path.join(settings.BASE_DIR, 'webfront', 'schemas')
    AVAILABLE_TEMPLATES = {}

    @classmethod
    def load_templates(cls):
        """Load all JSON schema files from TEMPLATE_DIR."""
        cls.AVAILABLE_TEMPLATES = {}
        for filename in os.listdir(cls.TEMPLATE_DIR):
            if filename.endswith('.json'):
                key = filename.replace('.json', '')
                with open(os.path.join(cls.TEMPLATE_DIR, filename), 'r') as f:
                    data = json.load(f)
                    cls.AVAILABLE_TEMPLATES[key] = {
                        'title': filename,
                        'schema': data
                    }

    @classmethod
    def get_available_templates(cls):
        if not cls.AVAILABLE_TEMPLATES:
            cls.load_templates()
        return list(cls.AVAILABLE_TEMPLATES.keys())

    @classmethod
    def validate(cls, template_key, config_json):
        if not cls.AVAILABLE_TEMPLATES:
            cls.load_templates()

        if template_key not in cls.AVAILABLE_TEMPLATES:
            raise ValidationError(f"Unknown template: '{template_key}'")

        schema = cls.AVAILABLE_TEMPLATES[template_key]['schema']
        try:
            jsonschema_validate(instance=config_json, schema=schema)
        except JSONSchemaValidationError as e:
            raise ValidationError(f"Validation error for '{template_key}': {e.message}")

