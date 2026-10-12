"""The host's numbers: the cap, the first balance, the prices.

A file, as the plans are (``toto.subscriptions.plans``): what an action costs
is a decision of whoever runs the platform, read once a process and checked
when the platform starts (``checks.py``), never a table somebody edits on a
page. ``settings.DISCIPLINE_FILE`` names it; the library's own
``discipline.yaml`` beside this module is read where a host names none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

DEFAULT_FILE = Path(__file__).parent / "discipline.yaml"
_SCHEMA_VERSION = 1
_ALLOWED_TOP = {"version", "cap", "signup", "default_regen_per_hour", "prices", "auras"}
_ALLOWED_AURA = {"name", "slug", "description", "regen_per_hour", "cap", "communities"}


class ConfigError(ValueError):
    """The file is missing or malformed."""


@dataclass(frozen=True)
class Config:
    cap: Decimal = Decimal("100")
    signup: Decimal = Decimal("0")
    default_regen_per_hour: Decimal = Decimal("0")
    prices: dict = field(default_factory=dict)
    #: Auras the ingress makes and the communities it gives them to.
    auras: tuple = ()


_CONFIG: Config | None = None
_SOURCE: Path | None = None


def source_path() -> Path:
    from django.conf import settings

    override = getattr(settings, "DISCIPLINE_FILE", "")
    return Path(override) if override else DEFAULT_FILE


def _read(path: Path) -> dict:
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"no Discipline file at {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a mapping, not {type(raw).__name__}")
    return raw


def _number(value):
    """A non-negative Decimal, or None when ``value`` is not one. A bool is
    an int in Python and YAML's ``true`` is not a number here."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def _problems(raw: dict) -> list[str]:
    found = []
    unknown = set(raw) - _ALLOWED_TOP
    if unknown:
        found.append(f"unknown top-level key(s): {sorted(unknown)}")
    if type(raw.get("version")) is not int or raw.get("version") != _SCHEMA_VERSION:
        found.append(f"version must be {_SCHEMA_VERSION}, got {raw.get('version')!r}")
    for key in ("cap", "signup", "default_regen_per_hour"):
        if key in raw and _number(raw[key]) is None:
            found.append(f"{key} must be a number, zero or more, not {raw[key]!r}")

    prices = raw.get("prices", {})
    if prices is None:
        prices = {}
    if not isinstance(prices, dict):
        found.append("prices must be a mapping of metric code: mana per unit")
    else:
        from toto.quota.metrics import registry

        known = set(registry.codes())
        for code, price in prices.items():
            if _number(price) is None:
                found.append(f"prices[{code!r}] must be a number, zero or more, not {price!r}")
            # Only once the apps have declared their metrics: before that the
            # registry is empty and every code would look unknown.
            if known and code not in known:
                found.append(f"prices[{code!r}]: no installed app meters {code!r}")

    auras = raw.get("auras", [])
    if auras is None:
        auras = []
    if not isinstance(auras, list):
        found.append("auras must be a list")
    else:
        slugs = set()
        for index, entry in enumerate(auras):
            where = f"auras[{index}]"
            if not isinstance(entry, dict):
                found.append(f"{where} must be a mapping")
                continue
            extra = set(entry) - _ALLOWED_AURA
            if extra:
                found.append(f"{where}: unknown key(s) {sorted(extra)}")
            if not entry.get("name") or not entry.get("slug"):
                found.append(f"{where}: name and slug are required")
            elif entry["slug"] in slugs:
                found.append(f"{where}: the slug {entry['slug']!r} is used twice")
            slugs.add(entry.get("slug"))
            if _number(entry.get("regen_per_hour")) is None:
                found.append(f"{where}: regen_per_hour must be a number, zero or more")
            if entry.get("cap") is not None and _number(entry["cap"]) is None:
                found.append(f"{where}: cap must be a number, zero or more, or left out")
            communities = entry.get("communities", [])
            if not isinstance(communities, list) or not all(isinstance(c, str) for c in communities):
                found.append(f"{where}: communities must be a list of community slugs")
        named = [c for entry in auras if isinstance(entry, dict)
                 for c in (entry.get("communities") or []) if isinstance(c, str)]
        twice = sorted({c for c in named if named.count(c) > 1})
        if twice:
            found.append(f"auras: a community has one aura; {twice} is given more than one")
    return found


def validate(path: Path | None = None) -> list[str]:
    """Error strings for the file, or ``[]``. Never raises for content."""
    try:
        raw = _read(path or source_path())
    except ConfigError as exc:
        return [str(exc)]
    return _problems(raw)


def _build(raw: dict) -> Config:
    defaults = Config()
    return Config(
        cap=_number(raw.get("cap", defaults.cap)),
        signup=_number(raw.get("signup", defaults.signup)),
        default_regen_per_hour=_number(raw.get("default_regen_per_hour",
                                               defaults.default_regen_per_hour)),
        prices={str(code): _number(price) for code, price in (raw.get("prices") or {}).items()},
        auras=tuple(dict(entry) for entry in (raw.get("auras") or [])),
    )


def load(force: bool = False) -> Config:
    """The numbers, read once a process (and again when the path changes,
    which is how a test names another file)."""
    global _CONFIG, _SOURCE
    path = source_path()
    if _CONFIG is None or force or path != _SOURCE:
        raw = _read(path)
        problems = _problems(raw)
        if problems:
            raise ConfigError(f"{path} is not usable: " + "; ".join(problems))
        _CONFIG, _SOURCE = _build(raw), path
    return _CONFIG


def reset() -> None:
    global _CONFIG, _SOURCE
    _CONFIG = _SOURCE = None
