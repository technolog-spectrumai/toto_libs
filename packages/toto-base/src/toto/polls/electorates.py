"""The registry of who may vote, and with what weight.

An electorate is policy code (see :class:`toto.polls.core.Electorate`); a
Question stores only a KEY under ``metadata["electorate"]`` — the convention
Business Center established — and this registry turns the key back into the
policy. Consumers register theirs from ``<app>/electorates.py``, discovered
the way antivirus discovers scanners: **registration is pure data.** This
module is imported from ``PollsConfig.ready()``, which runs before migrations
and during ``collectstatic``, so nothing here may touch the database at import
time — factories defer every lookup to call time.

Failure is asymmetric on purpose. A poll with a broken key falls back to
:class:`~toto.polls.core.OpenToAll` — a preference survey that refuses
everybody is worse than one that admits everybody. A FORMAL VOTE with a broken
key fails closed (:class:`NullElectorate`): a governance instrument whose roll
cannot be resolved must refuse ballots, not silently become a free-for-all.
"""

from __future__ import annotations

from django.utils.translation import gettext as _

from .core import Eligibility, OpenToAll

#: The metadata key naming which roll a question uses. Canonical home; the
#: Business Center module re-imports it.
ELECTORATE_KEY = "electorate"

#: key -> factory(question) -> Electorate. A factory rather than an instance
#: because company electorates need the question's scope to find their company.
_REGISTRY: dict[str, callable] = {}
#: scope_type -> default key, for questions whose metadata names no roll.
_SCOPE_DEFAULTS: dict[str, str] = {}


class DuplicateElectorate(ValueError):
    """Two factories claimed one key — one of them would never run."""


def register(key: str, factory) -> None:
    """Claim a key. Idempotent for the same callable, loud otherwise."""
    existing = _REGISTRY.get(key)
    if existing is not None and existing is not factory:
        raise DuplicateElectorate(f"{key!r} is already registered by {existing!r}")
    _REGISTRY[key] = factory


def register_scope_default(scope_type: str, key: str) -> None:
    _SCOPE_DEFAULTS[scope_type] = key


def registered_keys() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def key_of(question) -> str:
    """The key :func:`resolve` will use, without instantiating anything."""
    return (question.metadata or {}).get(ELECTORATE_KEY) or \
        _SCOPE_DEFAULTS.get(question.scope_type) or \
        ("all" if not question.scope_type else "")


class SnapshotElectorate:
    """The frozen register, answering for a vote that has one.

    Once a roll exists, it IS the electorate: standing and size come from the
    RollEntry rows the vote froze when it opened, whatever the configured
    Electorate looks like today. This is the generic form of the company
    record date, and it wins over every registry key on purpose.
    """

    def __init__(self, question):
        self.question = question

    def standing(self, question, user) -> Eligibility:
        if question.pk != self.question.pk:
            return Eligibility(False, reason=_(
                "This register belongs to another vote."))
        if not getattr(user, "is_authenticated", False):
            return Eligibility(False, reason=_("Sign in to vote."))

        from .electorate_models import RollEntry

        entry = RollEntry.objects.filter(question=question,
                                         user=user).first()
        if entry is None:
            return Eligibility(False, reason=_(
                "You were not on the register when this vote opened."))
        return Eligibility(True, weight=entry.weight)

    def size(self, question) -> int:
        from .electorate_models import RollEntry

        return RollEntry.objects.filter(question=self.question).count()


def has_roll(question) -> bool:
    from .electorate_models import RollEntry

    return RollEntry.objects.filter(question=question).exists()


def resolve(question):
    """Key -> Electorate, failing closed for formal votes.

    Order: the question's own metadata key, then the scope's default, then —
    for the global scope only — "all". A factory that raises is treated the
    same as a missing key: the caller gets an answer, never a stack trace,
    because standing() is consulted on every page view.
    """
    # A frozen register beats every key: the vote already named its people.
    if question.is_formal and has_roll(question):
        return SnapshotElectorate(question)

    key = key_of(question)

    factory = _REGISTRY.get(key)
    if factory is not None:
        try:
            return factory(question)
        except Exception:
            pass
    if question.is_formal:
        return NullElectorate(key)
    return OpenToAll()


class NullElectorate:
    """The fail-closed roll: nobody is eligible, and the reason says why."""

    def __init__(self, key: str = ""):
        self.key = key

    def standing(self, question, user) -> Eligibility:
        return Eligibility(False, reason=_(
            "This vote's electorate could not be resolved. "
            "Nobody may ballot until it is."))

    def size(self, question) -> int:
        return 0


class AllActiveUsers:
    """Every active account, one vote each.

    Unlike OpenToAll this has a real size(), so a formal vote's turnout is a
    fraction instead of the unknowable None.
    """

    def standing(self, question, user) -> Eligibility:
        if user is None or not getattr(user, "is_authenticated", False):
            return Eligibility(False, reason=_("Sign in to vote."))
        if not user.is_active:
            return Eligibility(False, reason=_("This account is not active."))
        return Eligibility(True, weight=1)

    def size(self, question) -> int:
        from django.contrib.auth import get_user_model
        return get_user_model().objects.filter(is_active=True).count()


class StaffElectorate:
    """Operators only, one vote each."""

    def standing(self, question, user) -> Eligibility:
        if user is None or not getattr(user, "is_authenticated", False):
            return Eligibility(False, reason=_("Sign in to vote."))
        if not (user.is_staff or user.is_superuser):
            return Eligibility(False, reason=_("Only staff vote on this."))
        return Eligibility(True, weight=1)

    def size(self, question) -> int:
        from django.contrib.auth import get_user_model
        from django.db.models import Q
        return get_user_model().objects.filter(
            Q(is_staff=True) | Q(is_superuser=True), is_active=True).count()


register("all", lambda question: AllActiveUsers())
register("staff", lambda question: StaffElectorate())
register_scope_default("", "all")
