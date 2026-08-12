"""Socialhub's levy provider: one weighted head per member, per day.

The head tax. Unlike every other provider here, the resource is not a thing
anybody accumulated — it is membership itself, so ``sample()`` walks people
rather than possessions and everybody in it holds exactly one head.

**The weight is the whole mechanism.** A person counts as their best
community's ``head_weight``: 0 for a trusted community (the exemption
``is_federal_tribe`` promised for years), 1 for an ordinary one, more for a
community the platform trusts less. Across several communities the lowest wins,
because belonging to a good community is what is supposed to be worth having.
Price stays one number for everyone; only the quantity moves.

Imported only by ``toto.tax``'s autodiscovery, so on a host with no levy engine
this module is never loaded and nobody is charged for existing.
"""

from __future__ import annotations

from decimal import Decimal

from toto.quota.levy import LevyProvider, registry

#: Weights are decimals ("0.5 of a head" is a sensible rate) and raw amounts are
#: integers, so raw is hundredths of a head. 100 raw = one head = one billing
#: unit, and the levy divides by this to get what it prices.
RAW_PER_HEAD = 100


class HeadTax(LevyProvider):
    code = "socialhub.head"
    metric_code = "civics.head"
    raw_per_unit = RAW_PER_HEAD
    consequence_text = (
        "you will not be able to add anything new until it clears — nothing "
        "you have is deleted, and you stay a member"
    )

    def format_raw(self, raw: int) -> str:
        heads = Decimal(raw) / RAW_PER_HEAD
        return f"{heads:g} head" if heads == 1 else f"{heads:g} heads"

    def sample(self):
        """Every member with a login, and what they count as.

        Two queries whatever the size of the platform: the weights of the few
        communities that set one, then every membership. A person in no
        community, or in one with no privilege row, is an ordinary head — this
        is a tax on everyone, not a penalty for being unaffiliated.
        """
        from toto.people.models import Person

        from .models import CommunityPrivilege
        from .privileges import ORDINARY_HEAD_WEIGHT

        weighted = dict(CommunityPrivilege.objects
                        .exclude(head_weight=ORDINARY_HEAD_WEIGHT)
                        .values_list("community_id", "head_weight"))

        best: dict[int, Decimal] = {}
        for user_id, community_id in (Person.objects
                                      .filter(user__isnull=False)
                                      .values_list("user_id", "communities")):
            weight = ORDINARY_HEAD_WEIGHT
            if community_id is not None:
                weight = weighted.get(community_id, ORDINARY_HEAD_WEIGHT)
            if user_id not in best or weight < best[user_id]:
                best[user_id] = weight

        for user_id, weight in best.items():
            raw = int(weight * RAW_PER_HEAD)
            if raw > 0:      # a weight of 0 is an exemption: not sampled at all
                yield user_id, raw

    def measure(self, user) -> int:
        """This user's own head, for the estimate on their page."""
        from .privileges import head_weight_for

        return int(head_weight_for(user) * RAW_PER_HEAD)


registry.register(HeadTax())
