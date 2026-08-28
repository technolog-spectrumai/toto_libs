"""The voting core: one set of rules, two very different things built on it.

This app answers two questions that look alike and are not:

* a **poll** — lightweight, data-gathering. Anyone who can see it may answer,
  the answer may be changed while it is open, and the point is the shape of the
  room's opinion.
* a **vote** — a formal decision. Who may vote is decided in advance, a ballot
  is cast ONCE and never edited, each ballot carries the weight it had at the
  moment it was cast, and the record has to survive being argued about later.

What they share is everything mechanical: a question, its options, a window
between opening and closing, one ballot per voter, a tally, and a record of who
did what and when. That shared part lives here, and NOTHING here knows what a
company or a chat room is.

**The differences are policy, not plumbing.** They are expressed as three things
a caller supplies, never as branches inside the engine:

``Electorate``   who may vote, and with what weight.
``Revisability`` whether a cast ballot may be changed while the question is open.
``Visibility``   who may read the tally, and when.

That is what lets one engine serve a chat-room poll and a shareholder
resolution without either pretending to be the other. The alternative — an
``if kind == "vote"`` ladder threaded through the models — is the thing this
module exists to avoid, because every consumer added afterwards makes it longer.

**Weight is snapshotted at cast time.** Business Center's existing ballots
already do this, and the reason generalises: shares change hands, room
membership changes, and a tally that recomputed weights on read would silently
rewrite the past every time it was displayed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class VotingError(Exception):
    """Base for every refusal the engine makes. Carries a reader-facing reason."""


class NotOpen(VotingError):
    """Cast attempted before it opened or after it closed."""


class NotEligible(VotingError):
    """This voter is not in the electorate."""


class AlreadyCast(VotingError):
    """A ballot exists and this question does not allow changing it."""


class UnknownChoice(VotingError):
    """The chosen option does not belong to this question."""


# ---------------------------------------------------------------------------
# The three policies
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Eligibility:
    """One voter's standing: may they vote, and for how much.

    ``weight`` is only meaningful when ``allowed``. A weight of zero is a real
    answer — a shareholder with no shares may attend and not count — and it is
    deliberately distinct from ``allowed=False``, which is "you are not part of
    this electorate at all". The two produce different sentences on screen.
    """

    allowed: bool
    weight: int = 1
    #: Why not, when not. Shown to the person, so it is a sentence.
    reason: str = ""


class Electorate(Protocol):
    """Who may vote. Supplied by whoever owns the question.

    Deliberately a protocol rather than a model field: the standalone Polls app
    asks "are you logged in", the Forum asks "are you in this room", and
    Business Center asks "do you hold shares in this company on the day the
    question opened". None of those can be expressed as a foreign key, and all
    three have to coexist.
    """

    def standing(self, question, user) -> Eligibility:
        """This user's standing in this question's electorate."""

    def size(self, question) -> int:
        """How many voters the electorate holds, for quorum and turnout.

        Zero means "unknown / unbounded" — an open poll has no roll to count,
        and reporting a turnout percentage against a made-up denominator is
        worse than reporting none.
        """


class OpenToAll:
    """Everyone signed in, one vote each. The lightweight-poll default."""

    def standing(self, question, user) -> Eligibility:
        if not getattr(user, "is_authenticated", False):
            return Eligibility(False, reason="You have to be signed in to answer.")
        return Eligibility(True, weight=1)

    def size(self, question) -> int:
        return 0


# ---------------------------------------------------------------------------
# The rules a question carries
# ---------------------------------------------------------------------------

class Revisability:
    """May a cast ballot be changed while the question is still open?"""

    #: A poll. Change your mind as often as you like until it closes.
    OPEN = "open"
    #: A vote. Cast once; the ballot is a record, not a preference.
    FINAL = "final"

    CHOICES = [
        (OPEN, "Answers may be changed while open"),
        (FINAL, "A ballot is cast once and is final"),
    ]


class Visibility:
    """Who may read the tally, and when."""

    #: Running totals, visible to anyone who may see the question.
    LIVE = "live"
    #: Nothing until it closes. The default for anything called a vote: a
    #: running tally in a formal decision is an instrument for changing it.
    ON_CLOSE = "on_close"
    #: Only after it closes, and only to the electorate.
    ON_CLOSE_PRIVATE = "on_close_private"

    CHOICES = [
        (LIVE, "Results visible while open"),
        (ON_CLOSE, "Results visible once closed"),
        (ON_CLOSE_PRIVATE, "Results visible once closed, to voters only"),
    ]


# ---------------------------------------------------------------------------
# Tally
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Result:
    """One option's standing in the count."""

    choice_id: int
    label: str
    text: str
    #: Sum of the WEIGHTS cast for this option, not the number of ballots.
    weight: int
    #: How many ballots, regardless of weight. Both are reported because in a
    #: weighted vote they answer different questions, and showing only one is
    #: how "3 of 5 shareholders agreed" becomes "60% approved".
    ballots: int

    @property
    def is_empty(self) -> bool:
        return self.ballots == 0


@dataclass(frozen=True)
class Tally:
    """A finished count. Immutable, and it knows what it does not know."""

    results: tuple
    #: Total weight cast across every option.
    total_weight: int
    #: Total ballots cast.
    total_ballots: int
    #: Size of the electorate, or 0 when it has no roll to count.
    electorate: int

    @property
    def turnout(self):
        """Fraction of the electorate that voted, or None when unknowable.

        None rather than zero: an open poll has no denominator, and a zero
        turnout reads as "nobody voted" rather than "the question does not have
        a fixed roll".
        """
        if not self.electorate:
            return None
        return self.total_ballots / self.electorate

    def share(self, result: Result):
        """This option's share of the weight cast, or None when nothing is in."""
        if not self.total_weight:
            return None
        return result.weight / self.total_weight

    @property
    def winner(self):
        """The single leading option, or None on a tie or an empty count.

        None on a tie is deliberate. Picking one of two equal options — by id,
        by label, by whatever the database returned first — would invent a
        result, and a tie in a formal vote is a real outcome with its own
        consequences in the rules that govern it.
        """
        if not self.total_ballots:
            return None
        best = max(self.results, key=lambda r: r.weight)
        if sum(1 for r in self.results if r.weight == best.weight) > 1:
            return None
        return best
