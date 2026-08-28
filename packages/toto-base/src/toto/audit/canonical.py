"""Canonical JSON, so the same facts always hash to the same digest.

Copied from the parked Django Irena's ``toto.audit.canonical``. Three settings
carry the whole guarantee: ``sort_keys`` (dict order must not matter),
``separators`` (no incidental whitespace) and ``ensure_ascii=False`` (a Polish
name hashes as itself, not as an escape sequence — and hashes the same on a
host with a different default encoding).

``Decimal`` becomes a string, never a float. A share count that round-trips
through a float is a share count that can change.
"""

import hashlib
import json
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder


class AuditJSONEncoder(DjangoJSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def canonical_json(value):
    return json.dumps(
        value,
        cls=AuditJSONEncoder,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def payload_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
