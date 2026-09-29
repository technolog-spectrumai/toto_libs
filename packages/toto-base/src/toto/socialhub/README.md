# toto.socialhub

Communities and the people in them: the community directory and pages,
organisation charts, people's profiles, joining by endorsement (apply, prove
you are human, name a referee, wait for them to accept), and what a community
grants its members (`CommunityPrivilege`, see [PRIVILEGES.md](PRIVILEGES.md)).
Membership itself is one relation, `people.Person.communities`, and every app
that asks "who is in this community" reads it.

## Functional communities and circles — orthogonal on purpose

Since 2026-09-28 a community is one of two kinds, told apart by one column,
`Community.is_circle`:

| | Functional community | Circle |
|---|---|---|
| Examples | `devs`, `testers` | `seniors`, `newcomers`, `board`, `c-suite` |
| Carries | plan offers, discounts, privileges | who may **read** wiki pages, and how fast its members' **mana refills** (`regen_security`/`regen_compute`/`regen_storage`, per hour; blank = the pool's own) |
| Joined | by application, accepted by a referee | through the admin, by a superuser |
| Shown to members | yes — directory, profiles, map, API | never; its page is a 404 to them |
| Tree (`parent`) | offers inherit down it | none: a circle has no parent and is no parent |
| How many | any number | at most **seven** on a platform (`MAX_CIRCLES`): an eighth, or a community turned into one at the cap, is refused on save |

A person is usually in one of each. A senior engineer is in `devs` and
`seniors`; the CTO in `devs` and `board`; a junior tester in `testers` and
`newcomers`. What they may buy and do comes from the first, what they may read
— and how fast their mana comes back — from the second, and neither says
anything about the other.

**A design feature.** One list of people serves both axes —
`Person.communities` — so nothing is copied into a second membership table, no
parallel ACL has to be kept in step with the first, and admitting or removing
somebody is one change the whole platform sees at once.

**A cybersecurity feature: defence in depth.** A mistake or a compromise on the
money axis — a wrong offer, a discount, a plan, a privilege — cannot open a
page, and a mistake on the reading axis — putting somebody in the wrong circle —
cannot buy anything or grant a right. Each axis refuses the other in several
layers, so no single forgotten check crosses them:

| Layer | Circles on the money axis |
|---|---|
| Forms and the admin | offers, discounts, privileges and applications name functional communities only (`limit_choices_to`); a circle's admin page has no privilege inline, and a new circle posted with one is refused |
| Models | `CommunityPlanOffer`, `CommunityDiscount` and `CommunityPrivilege` refuse a circle in `clean()` and `save()`; `MembershipApplication.clean` refuses one; accepting a reference into one is refused (`ReferenceRequest.save`, and the Accept button) |
| Services | `subscriptions.set_offers` and `set_discounts` ignore a circle's field, as they ignore an unknown one |
| Resolvers | `_offered_keys` and its parent walk, `best_discount`, `offering_communities` and `privileges.has_privilege` read functional communities only, so a row left on a circle grants nothing |
| Pages | the plans' Communities and Discounts tabs list no circle |
| Changing kind | `Community.clean` refuses making a community a circle while it carries an offer, a discount or a privilege, refuses a tree that mixes the kinds, and refuses a refill speed on a functional community (so a circle with speeds cannot quietly become one) |
| Refill speed | only a circle's speeds are read (`toto.mana.services.circle_speeds` filters `is_circle`), so a speed written on a functional community by hand refills nobody faster |

The reading half is the wiki's: a page names its circles and is read by the
members of any one of them, and a functional community grants no page. It is
enforced where the pages are (zenobia's `toto.wiki`), not here.

### Where circles are hidden

`Community.objects.listed_for(user)` is the one rule: every community for a
superuser, the functional ones for anybody else. The directory, a community's
page, its org chart data, the Administrata view and the chain graph, the chips
on a profile and on the roster, the JSON API, the People map's community
filter, the account book's community filters and the news doors all ask it, so
a listing never links to a page that answers 404. The workflow connectors run for nobody in particular and read
functional communities only. A superuser sees circles in the directory and on
profiles, marked as circles.

### Who puts people in circles

Superusers, on the **Circles** tab of this app (`communities/circles/`,
2026-09-29 — beside Profiles and Communities in the strip, shown to
superusers only): every circle with its members and its mana refill speeds;
make one (at most seven), add and remove people, set the speeds, remove a
circle nothing reads through any more. What a circle READS is each app's own
business and stays in that app (the wiki's page × circle grid). Or in the
Django admin: on the circle's own page (its **Members**
field, written through `community.members.set`, the relation's own door) or on
the person's page (their communities). Never the membership application — the
form offers no circle, and accepting a reference into one is refused.
Membership is direct: a circle has no parent, so nobody reads through a
community above or below it.

`Community.objects.functional()` and `.circles()` name the two kinds in
queries; `is_circle` defaults to False, so every community that existed before
it is functional.

## On the audit chain

Since 2026-09-28 every community and circle operation is a record on the
platform's audit chain (`audit.py`, signals — so the admin, the membership
flow, the wiki's Circles page and a shell are all covered): a community made,
changed (the fields, before and after) or removed; a person added to or taken
out of a community or circle, from either side of `Person.communities` and
through a `clear()`; senior members; privileges; an application submitted and
each of its steps; a reference asked for, given (the applicant admitted) or
declined. Every record says whether the community is a **circle** — the chain
is where a crossing of the two axes would show. Sign-ins, sign-outs and
accounts are recorded by `toto.audit.identity`. See `toto/audit/README.md`.

