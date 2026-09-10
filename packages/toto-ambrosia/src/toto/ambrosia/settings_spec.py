"""What a workspace setting IS, and how the base validates one it cannot read.

Ambrosia stores every lab's settings and knows what none of them mean. Each
language app declares its own fields from `AppConfig.ready()` (see
`registry.WorkspaceApp.settings_fields`) and this module turns that declaration
into validation, clamping and the data the panel renders — generically, with no
lab name anywhere in it. That keeps the seam pointing the same way as the rest
of the registry: the specialised apps know the base, never the reverse.

A field's bounds can come from a `toto.quota.times` dial (`dial_key`), which is
where the two timeouts that were already declared as time dials get their
floor and ceiling. See `limits.py` for why the ceiling is the cap even on a
host with no ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any, Callable

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# The kinds a field can be. Deliberately small: every one of these renders as a
# single control in the panel and validates in a few lines below.
SECONDS = "seconds"     # positive int, bounds usually from a time dial
INT = "int"             # positive int with explicit bounds
BOOL = "bool"
CHOICE = "choice"
ENV = "env"             # a flat {name: value} mapping of environment variables

#: Environment variable names must look like names — the kernel is handed these
#: verbatim, and a key with '=' or whitespace in it is a mistake that would
#: otherwise land in the process environment unnoticed.
_ENV_NAME = r"^[A-Za-z_][A-Za-z0-9_]*$"
MAX_ENV_VARS = 40
MAX_ENV_VALUE = 4096


@dataclass(frozen=True)
class Field:
    key: str
    kind: str
    label: Any                      # translatable
    default: Any = None
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[tuple[str, Any], ...] = ()
    # CHOICE only: the options when they depend on the WORKSPACE rather than
    # on the lab — a Compute Capsule is one of the owner's reservations, and that
    # list changes every time they reserve or release one. Called with the
    # workspace; wins over `choices` when set. Kept as a callable on the
    # field, like `bounds()`, so the base stays ignorant of what the options
    # are and who supplies them.
    choices_for: Callable[[Any], tuple[tuple[str, Any], ...]] | None = None
    dial_key: str = ""              # bounds come from this toto.quota.times dial
    needs_execute: bool = False     # changing it needs permissions.can_execute
    restart_hint: bool = False      # takes effect on the next kernel start
    help_text: Any = ""

    def bounds(self, workspace) -> tuple[int | None, int | None]:
        """The (minimum, maximum) in force for this workspace, right now.

        A dial-backed field asks limits.py, so the answer tracks whether this
        host has a ledger. Everything else uses its declared pair.
        """
        if self.dial_key:
            from . import limits

            return limits.allowed_range(self.dial_key, workspace)
        return self.minimum, self.maximum

    def options(self, workspace) -> tuple[tuple[str, Any], ...]:
        """The (value, label) pairs this workspace may choose from, right now."""
        if self.choices_for is not None:
            return tuple(self.choices_for(workspace))
        return self.choices

    def fallback(self, workspace) -> Any:
        """The value in force when nothing is stored.

        A callable default is a FACTORY (``default=dict``), called here — the
        alternative is a mutable shared between every workspace, and a bare
        type reaching the config island, where json refuses it.
        """
        if self.dial_key:
            from . import limits

            return limits.dial_default(self.dial_key, workspace)
        return self.default() if callable(self.default) else self.default


def _clean_int(field: Field, value: Any, workspace) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError({field.key: _("Enter a whole number.")})
    low, high = field.bounds(workspace)
    if low is not None and number < low:
        raise ValidationError({field.key: _("Must be at least %(low)s.") % {"low": low}})
    if high is not None and number > high:
        raise ValidationError({field.key: _("Must be at most %(high)s.") % {"high": high}})
    return number


def _clean_bool(field: Field, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in ("1", "true", "True", 1):
        return True
    if value in ("0", "false", "False", 0):
        return False
    raise ValidationError({field.key: _("Enter true or false.")})


def _clean_choice(field: Field, value: Any, workspace) -> str:
    allowed = [choice for choice, _label in field.options(workspace)]
    if value not in allowed:
        raise ValidationError({field.key: _("Choose one of: %(allowed)s.")
                               % {"allowed": ", ".join(str(a) for a in allowed)}})
    return value


def _clean_env(field: Field, value: Any) -> dict[str, str]:
    import re

    if not isinstance(value, dict):
        raise ValidationError({field.key: _("Expected a mapping of names to values.")})
    if len(value) > MAX_ENV_VARS:
        raise ValidationError({field.key: _("At most %(count)s variables.")
                               % {"count": MAX_ENV_VARS}})
    cleaned: dict[str, str] = {}
    for name, raw in value.items():
        name = str(name).strip()
        if not re.match(_ENV_NAME, name):
            raise ValidationError({field.key: _(
                "%(name)r is not a valid variable name — letters, digits and "
                "underscores only, not starting with a digit.") % {"name": name}})
        text = "" if raw is None else str(raw)
        if len(text) > MAX_ENV_VALUE:
            raise ValidationError({field.key: _("%(name)s is too long.") % {"name": name}})
        # PostgreSQL's jsonb cannot hold a NUL anywhere in a string, so a NUL
        # here is a 500 from the database on a deployed host while sqlite (the
        # test database) takes it happily. Refuse it where it can still be a
        # sentence. A NUL is also not something the environment can carry:
        # execve truncates at the first one.
        if "\x00" in text:
            raise ValidationError({field.key: _(
                "%(name)s contains a null character, which an environment "
                "variable cannot hold.") % {"name": name}})
        cleaned[name] = text
    return cleaned


def clean(fields: tuple[Field, ...], payload: dict, *, workspace,
          may_execute: bool = True) -> dict:
    """Validate a payload against a lab's declared fields.

    Only keys the lab declared survive; an unknown key is an error rather than
    a silent drop, because a typo'd setting that reads as "saved" and does
    nothing is the worst outcome available here.
    """
    if not isinstance(payload, dict):
        # A list of declared keys would otherwise sail through the set
        # arithmetic below and crash on .items() as a 500.
        raise ValidationError(_("Expected a mapping of settings to values."))

    by_key = {f.key: f for f in fields}
    unknown = sorted(set(payload) - set(by_key))
    if unknown:
        raise ValidationError(
            _("Not a setting of this workspace: %(keys)s.") % {"keys": ", ".join(unknown)})

    cleaned: dict[str, Any] = {}
    errors: dict[str, list] = {}
    for key, value in payload.items():
        field = by_key[key]
        if field.needs_execute and not may_execute:
            errors[key] = [_("You may not change what runs on this platform.")]
            continue
        if value is None:
            # "No override" — which is exactly what `effective()` publishes for
            # an optional field nobody has set, so the panel can post back what
            # it was given. Recorded as a removal, not as a value.
            cleaned[key] = None
            continue
        try:
            if field.kind in (SECONDS, INT):
                cleaned[key] = _clean_int(field, value, workspace)
            elif field.kind == BOOL:
                cleaned[key] = _clean_bool(field, value)
            elif field.kind == CHOICE:
                cleaned[key] = _clean_choice(field, value, workspace)
            elif field.kind == ENV:
                cleaned[key] = _clean_env(field, value)
            else:                                   # pragma: no cover - guarded by review
                raise ValidationError({key: _("Unsupported setting type.")})
        except ValidationError as exc:
            errors.update(exc.message_dict if hasattr(exc, "message_dict")
                          else {key: exc.messages})
    if errors:
        raise ValidationError(errors)
    return cleaned


def effective(fields: tuple[Field, ...], stored: dict, *, workspace) -> dict:
    """Every declared field's value in force: stored where set, else the default.

    A stored value is re-clamped on the way out, so a bound that TIGHTENS later
    (a smaller ceiling, or a paid grant that lapsed on a billed host) is
    honoured immediately instead of waiting for the next save.
    """
    values: dict[str, Any] = {}
    for field in fields:
        if field.key in stored:
            value = stored[field.key]
            if field.kind in (SECONDS, INT):
                low, high = field.bounds(workspace)
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    values[field.key] = field.fallback(workspace)
                    continue
                if low is not None:
                    number = max(low, number)
                if high is not None:
                    number = min(high, number)
                value = number
            elif field.kind == CHOICE and field.choices_for is not None:
                # The same courtesy the ints get, for an option list that can
                # shrink underneath a stored value: a released Capsule must not
                # keep being "the setting" — it falls back to the default, and
                # the panel shows that rather than a uuid nothing resolves.
                if value not in [c for c, _l in field.options(workspace)]:
                    value = field.fallback(workspace)
            values[field.key] = value
        else:
            values[field.key] = field.fallback(workspace)
    return values


def describe(fields: tuple[Field, ...], *, workspace) -> list[dict]:
    """The panel's view of the fields: bounds and choices as plain data."""
    described = []
    for field in fields:
        low, high = field.bounds(workspace)
        described.append({
            "key": field.key,
            "kind": field.kind,
            "label": str(field.label),
            "help": str(field.help_text),
            "min": low,
            "max": high,
            "choices": [{"value": value, "label": str(label)}
                        for value, label in field.options(workspace)],
            "default": field.fallback(workspace),
            "needsExecute": field.needs_execute,
            "restartHint": field.restart_hint,
        })
    return described
