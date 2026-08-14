"""Who judges a count, and by what rule. The engine never does.

:mod:`toto.polls.core` deliberately stops at arithmetic — WINNER, TIE,
NO_BALLOTS. "Passed" is a POLICY: a fraction, a denominator, a strictness,
and it belongs to whoever owns the scope. Business Center's shareholder
resolution passes on ``yes/(yes+no)`` strictly above the company's threshold;
a chat-room poll has no such notion at all; a stage-7 consensus profile will
have several.

So a scope may register a JUDGE: a callable that reads a finished
:class:`~toto.polls.core.Tally` and returns a dict, which
:func:`~toto.polls.services.record_decision` stores verbatim under
``content["governance"]``. The engine supplies the numbers and stores the
answer; it never holds an opinion about it, and there is no
``if scope_type ==`` anywhere in this package.

**Separate from the electorate registry on purpose.** An electorate answers
"may you vote, and for how much" and is consulted on every page view for
every viewer; a judge answers "and what does that mean" and is consulted
once, when the decision is written. A company has two electorates
(shareholders, board) and one governance rule — hanging the rule off the roll
would either duplicate it or need a mixin to un-duplicate it.

Registration is pure data, discovered from ``<app>/governance.py`` by
``PollsConfig.ready()``, so nothing here may touch the database at import
time — the same contract the scanners and electorates registries keep.
"""

from __future__ import annotations

#: Question.metadata key naming which judge a question uses.
GOVERNANCE_KEY = "governance"

#: key -> judge(question, tally) -> dict | None
_REGISTRY: dict[str, callable] = {}
#: scope_type -> default key, for questions whose metadata names no judge.
_SCOPE_DEFAULTS: dict[str, str] = {}


class DuplicateJudge(ValueError):
    """Two judges claimed one key — one of them would never run."""


def register(key: str, judge) -> None:
    """Claim a key. Idempotent for the same callable, loud otherwise."""
    existing = _REGISTRY.get(key)
    if existing is not None and existing is not judge:
        raise DuplicateJudge(f"{key!r} is already judged by {existing!r}")
    _REGISTRY[key] = judge


def register_scope_default(scope_type: str, key: str) -> None:
    _SCOPE_DEFAULTS[scope_type] = key


def registered_keys() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def key_of(question) -> str:
    return (question.metadata or {}).get(GOVERNANCE_KEY) \
        or _SCOPE_DEFAULTS.get(question.scope_type) or ""


def judge(question, tally):
    """The scope's verdict on this count, or None when it has no rule.

    A plain callable rather than a factory: a judge needs no per-question
    construction, and one that carries parameters (stage 7) registers a bound
    method or a closure.

    **Deliberately not wrapped in try/except.** An electorate that raises must
    still answer, because standing() runs on every render and an error page is
    not an answer. A judge that raises is a bug in the RULE, and the honest
    response is to record nothing: record_decision runs this inside its
    transaction, so the close and the row roll back together and the vote can
    be recorded again once the rule is fixed. A decision is written once and
    cannot be withdrawn — a wrong one is worse than a missing one.
    """
    fn = _REGISTRY.get(key_of(question))
    return fn(question, tally) if fn is not None else None
