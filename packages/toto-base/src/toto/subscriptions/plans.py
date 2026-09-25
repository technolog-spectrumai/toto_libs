"""The plans this platform sells — generated from YAML, immutable, never rows.

A plan is a **read-only object built at import time from a validated file**.
It is deliberately not a database table and deliberately not a Python literal:

* Not a table, because a plan is a *decision about the product*, not user
  state. It belonged in the database only for as long as the admin was the
  editor, and that made the admin a second authoring path beside the seeder —
  two places to change one thing, with nothing keeping them in step.
* Not a Python literal, because the ladder is content: an operator should be
  able to re-price a tier without a code review, and a host should be able to
  ship a different ladder without a fork.

The shape is :mod:`toto.anastasia.families`'s, for the same reason that module
gives: **one table, read twice, so an early refusal and a late one can never
disagree**. The plans page and the subscribe endpoint read this registry, and
there is no third answer anywhere.

Validation runs at import (``SubscriptionsConfig.ready``) and again as a Django
system check, so a malformed file stops a deploy rather than surfacing as a
missing plan weeks later. :func:`validate` returns strings the way
``sql_neo4j_sync.loader.validate_configs`` does.

**A subscription stores a plan_key, not a foreign key.** Nothing here is
referenced by the database, which is what lets the ladder change without a
migration — and what makes a dangling key possible. That case is handled where
it belongs: :func:`get` answers ``None`` and ``plan_for`` falls back to the
default plan, so a person whose tier left the file loses access rather than
crashing, and their charge rows keep the name they paid under.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Where the shipped ladder lives. A host replaces it wholesale by pointing
#: ``settings.SUBSCRIPTION_PLANS_FILE`` at another file — REPLACE, never merge:
#: merging would re-open the duplicate-key question at runtime and make "what
#: does this host sell" a query with two answers.
DEFAULT_PLANS_FILE = Path(__file__).parent / "plans.yaml"

#: Same shape a Django slug takes, and the same shape the URL captures.
KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

_ALLOWED_TOP = {"version", "plans"}
_ALLOWED_PLAN = {"key", "name", "description", "units", "features",
                 "default", "order", "admin_only", "all_features"}
_SCHEMA_VERSION = 1


class PlanError(ValueError):
    """The file is malformed, or a lookup named a plan that is not in it."""


@dataclass(frozen=True)
class Plan:
    """One tier, exactly as the file declares it. Immutable by construction."""

    #: The stable identity. Appears in URLs, in Subscription rows, in the
    #: community offer table — everywhere except the screen.
    key: str
    #: What a person reads. NEVER a lookup key: renaming a tier must not
    #: orphan the people on it.
    name: str
    description: str = ""
    #: A signed QUANTITY per month, not a price. Negative is a stipend — the
    #: platform paying the subscriber. Money is the tariff rate card's job.
    units: int = 0
    #: Registered feature keys. A TUPLE: a list inside a frozen dataclass is
    #: exactly the hole "immutable" is meant to close.
    features: tuple[str, ...] = ()
    is_default: bool = False
    order: int = 100
    #: Only superusers see it, may hold it, and are put on it (1.51). Left out
    #: of every plan listing and the subscribe door for anyone else — refused
    #: on the server, not merely hidden.
    admin_only: bool = False
    #: Grants every feature, present and future, without listing them (1.51).
    all_features: bool = False

    def grants(self, feature_key: str) -> bool:
        return self.all_features or feature_key in self.features

    def feature_rows(self) -> list:
        """The paid features this HOST actually serves, in card order.

        Filtered through ``registry.installed()`` so a card never promises a
        room this build does not have.
        """
        from .catalogue import registry

        listed = set(self.features)
        return [e for e in registry.installed()
                if (self.all_features or e.feature_key in listed) and not e.free]


_REGISTRY: dict[str, Plan] = {}
_SOURCE: Path | None = None


def source_path() -> Path:
    """The file the registry reads, honouring the host override."""
    from django.conf import settings

    override = getattr(settings, "SUBSCRIPTION_PLANS_FILE", "")
    return Path(override) if override else DEFAULT_PLANS_FILE


def _read(path: Path) -> dict:
    import yaml

    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise PlanError(f"no plan file at {path}") from None
    except yaml.YAMLError as exc:
        raise PlanError(f"{path} is not valid YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise PlanError(f"{path} must be a mapping, not {type(raw).__name__}")
    return raw


def _problems(raw: dict, path: Path) -> list[str]:
    """Every reason this document cannot become a registry. Errors only."""
    from .catalogue import registry

    found: list[str] = []
    unknown_top = set(raw) - _ALLOWED_TOP
    if unknown_top:
        found.append(f"unknown top-level key(s): {sorted(unknown_top)}")
    if raw.get("version") != _SCHEMA_VERSION:
        found.append(f"version must be {_SCHEMA_VERSION}, got {raw.get('version')!r}")

    plans = raw.get("plans")
    if not isinstance(plans, list) or not plans:
        found.append("plans must be a non-empty list")
        return found

    declared = registry.declared_keys()
    free = registry.free_keys()
    seen: set[str] = set()
    defaults: list[str] = []

    for index, entry in enumerate(plans):
        where = f"plans[{index}]"
        if not isinstance(entry, dict):
            found.append(f"{where} must be a mapping, not {type(entry).__name__}")
            continue
        unknown = set(entry) - _ALLOWED_PLAN
        if unknown:
            found.append(f"{where}: unknown key(s) {sorted(unknown)}")

        key = entry.get("key")
        if not isinstance(key, str) or not KEY_RE.match(key or ""):
            found.append(f"{where}: key {key!r} must match {KEY_RE.pattern}")
        elif key in seen:
            found.append(f"{where}: duplicate plan_key {key!r}")
        else:
            seen.add(key)

        if not entry.get("name"):
            found.append(f"{where}: name is required")
        for numeric in ("units", "order"):
            if numeric in entry and not isinstance(entry[numeric], int):
                found.append(f"{where}: {numeric} must be a whole number")
        for flag in ("default", "admin_only", "all_features"):
            if flag in entry and not isinstance(entry[flag], bool):
                found.append(f"{where}: {flag} must be true or false")
        if entry.get("default") and entry.get("admin_only"):
            found.append(f"{where}: the default plan cannot be admin_only")
        if entry.get("default"):
            defaults.append(str(key))
            if int(entry.get("units", 0) or 0) > 0:
                found.append(f"{where}: the default plan cannot cost units")

        features = entry.get("features", [])
        if not isinstance(features, list):
            found.append(f"{where}: features must be a list")
        else:
            if len(set(features)) != len(features):
                found.append(f"{where}: features lists the same key twice")
            for feature in features:
                if not isinstance(feature, str):
                    found.append(f"{where}: feature {feature!r} must be a string")
                elif feature not in declared:
                    found.append(
                        f"{where}: unknown feature_key {feature!r} — "
                        f"nothing registers it")
                elif feature in free:
                    found.append(
                        f"{where}: {feature!r} is a FREE feature and cannot be "
                        "sold by a plan")

    if len(defaults) != 1:
        found.append(f"exactly one plan must set default: true, found {defaults}")
    return found


def validate(path: Path | None = None) -> list[str]:
    """Error strings for the file, or ``[]``. Never raises for content."""
    path = path or source_path()
    try:
        raw = _read(path)
    except PlanError as exc:
        return [str(exc)]
    return _problems(raw, path)


def _build(raw: dict) -> dict[str, Plan]:
    plans = {}
    for entry in raw["plans"]:
        plan = Plan(
            key=entry["key"],
            name=entry["name"],
            description=(entry.get("description") or "").strip(),
            units=int(entry.get("units", 0) or 0),
            features=tuple(entry.get("features", []) or ()),
            is_default=bool(entry.get("default", False)),
            order=int(entry.get("order", 100) or 100),
            admin_only=bool(entry.get("admin_only", False)),
            all_features=bool(entry.get("all_features", False)),
        )
        plans[plan.key] = plan
    return plans


def load(path: Path | None = None) -> None:
    """Read, validate and install the registry. Raises PlanError on any fault."""
    global _REGISTRY, _SOURCE

    path = path or source_path()
    problems = validate(path)
    if problems:
        raise PlanError(f"{path} is not a usable plan file:\n  - "
                        + "\n  - ".join(problems))
    _REGISTRY = _build(_read(path))
    _SOURCE = path


def reload() -> None:
    """Drop the registry so the next read re-resolves the source.

    The test seam, and the reason the cache is a module global rather than an
    ``lru_cache``: ``override_settings`` must be able to swap the file without
    a signal, which is the lesson ``catalogue.mounted_app_names`` records.
    """
    global _REGISTRY, _SOURCE

    _REGISTRY = {}
    _SOURCE = None


def _ensure() -> dict[str, Plan]:
    if not _REGISTRY or _SOURCE != source_path():
        load()
    return _REGISTRY


def plan(key: str) -> Plan:
    """The plan, or PlanError naming what IS valid."""
    found = _ensure().get(key)
    if found is None:
        raise PlanError(f"no plan {key!r}; this platform sells "
                        f"{sorted(_ensure())}")
    return found


def get(key: str) -> Plan | None:
    """The plan, or None. What runtime code uses — a key can go stale."""
    return _ensure().get(key)


def all_plans() -> tuple[Plan, ...]:
    return tuple(sorted(_ensure().values(),
                        key=lambda p: (p.order, p.units, p.name)))


def public_plans() -> tuple[Plan, ...]:
    """Every plan anybody who is not a superuser may be shown."""
    return tuple(p for p in all_plans() if not p.admin_only)


def admin_plan() -> Plan | None:
    """The plan superusers are put on, or None when the ladder has none."""
    return next((p for p in all_plans() if p.admin_only), None)


def keys() -> frozenset[str]:
    return frozenset(_ensure())


def default_plan() -> Plan:
    """Never None: the file is validated to carry exactly one default."""
    for candidate in all_plans():
        if candidate.is_default:
            return candidate
    raise PlanError("no default plan; the registry was not validated")
