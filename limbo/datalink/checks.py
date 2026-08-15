"""Django system checks, so `manage.py check` fails on a broken contract.

Registered under the "datalink" tag, which makes `manage.py check --tag datalink` a
cheap CI step; the ordinary `manage.py check` every clean-env gate already runs picks
it up per host profile too.

Errors only. The advisory notes from ``describe_registry`` — chiefly "these models have
no modification timestamp, so a both-changed conflict is reported rather than
auto-resolved" — are true of a healthy registry and cover most of the scope, so
emitting them here would print a paragraph of warnings on every `manage.py check` in
every gate. They belong to `manage.py datalink_check`, where somebody asked.
"""
from django.core.checks import Error, Tags, register


@register(Tags.models, "datalink")
def check_registry(app_configs, **kwargs):
    from .validate import validate_registry

    return [
        Error(problem, id="datalink.E001", hint="see toto/datalink/registry.py")
        for problem in validate_registry()
    ]
