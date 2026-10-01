"""What differs between the two Django versions the library runs on (2026-10-01).

zenobia runs Django 5.2; other hosts of this library still run 4.2. Code that
needs an API only one of them has asks here, so the difference is written in
one place and goes when 4.2 does.
"""
import django
from django.db import models


def check_constraint(*, condition, name, **kwargs):
    """A ``CheckConstraint`` on ``condition``, under either Django.

    Django 5.1 renamed the argument ``check`` to ``condition`` and 5.2 warns on
    the old name (6.0 removes it); 4.2 knows only ``check``. The constraint
    compares equal either way, so makemigrations sees no change from it.
    """
    if django.VERSION >= (5, 1):
        return models.CheckConstraint(condition=condition, name=name, **kwargs)
    return models.CheckConstraint(check=condition, name=name, **kwargs)


#: Keyword arguments for a ``forms.URLField`` that reads a URL typed without a
#: scheme as https://. Django 5.0 added ``assume_scheme`` (and warns until it
#: is given; 6.0 makes https the default); 4.2 has no such argument and keeps
#: http://, so there it is empty.
URLFIELD_HTTPS = {"assume_scheme": "https"} if django.VERSION >= (5, 0) else {}
