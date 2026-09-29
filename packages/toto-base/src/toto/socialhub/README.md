# toto.socialhub

Communities and the people in them: the community directory and pages,
organisation charts, people's profiles, joining by endorsement (apply, prove
you are human, name a referee, wait for them to accept), and what a community
grants its members (`CommunityPrivilege`, see [PRIVILEGES.md](PRIVILEGES.md)).
Membership itself is one relation, `people.Person.communities`, and every app
that asks "who is in this community" reads it.

## Communities and clearances — orthogonal on purpose

Since 2026-09-28 the platform has two kinds of group, and since 2026-09-29
they are two models: a **Community** (`devs`, `testers`) says what somebody
does here; a **Clearance** (`socialhub.Clearance` — `internal`,
`confidential`, `onboarding`) says what they are trusted to read. A
clearance is named after what it **opens**, never after who holds it.

| | Community | Clearance |
|---|---|---|
| Examples | `devs`, `testers` | `internal`, `confidential`, `onboarding` |
| Carries | plan offers, discounts, privileges, news, a page, a forum | who may **read** — wiki pages, files (sheets, decks), places, routes and map layers, through `clearance_access` — and how fast its holders' **mana refills** (`regen_security`/`regen_compute`/`regen_storage`, per hour; blank = the pool's own) |
| Membership | `Person.communities` | `Person.clearances` |
| Joined | by application, accepted by a referee | given by a superuser: the Clearances tab, the admin, the console |
| Shown to members | yes — directory, profiles, map, API | never; it has no page |
| Tree (`parent`) | offers inherit down it | none: a clearance stands alone, membership is direct |
| How many | any number | at most **seven** on a platform (`MAX_CLEARANCES`): an eighth is refused on save |

A person usually holds one of each. A senior engineer is in `devs` and
holds `internal`; the CTO is in `devs` and holds `confidential`; a junior
tester is in `testers` and holds `onboarding`. What they may buy and do
comes from the community, what they may read — and how fast their mana
comes back — from the clearance, and neither says anything about the other.

**A design feature.** Each axis is one relation on the person, and every app
that asks "who is in this community" or "who holds this clearance" reads
that relation: nothing is copied into a second membership table, no parallel
ACL has to be kept in step, and admitting or removing somebody is one change
the whole platform sees at once.

**A cybersecurity feature: defence in depth.** A mistake or a compromise on
the money axis — a wrong offer, a discount, a plan, a privilege — cannot open
a page, and a mistake on the reading axis — giving somebody the wrong
clearance — cannot buy anything or grant a right. The two are separate
tables, so no query over communities can ever return a clearance and no
plan, discount, privilege or application can name one: the type enforces
what a flag would have had to police at every use site. What remains is
where each axis is READ: the resolvers (`_offered_keys`, `best_discount`,
`offering_communities`, `privileges.has_privilege`) read `Person.communities`
only, and `clearance_access` and `toto.mana.services.clearance_speeds` read
`Person.clearances` only. Joining a community — the one self-service door on
the platform — can therefore never grant access to anything.

The reading half belongs to each app that keeps things to clearances — the
wiki's pages (zenobia's `toto.wiki`), the vault's files (sheets, decks, and
every other type), locations' routes and map layers, zenobia's places — and
is enforced where the things are. They share one rule, `clearance_access.py`
(2026-09-29): an object kept to no clearance follows the app's own rule; one
kept to clearances is read by their holders, its owner and superusers, and
by nobody else — a clearance both keeps and grants — and a hidden object
answers as a missing one. Each app keeps its own "who reads this" control
inside itself; the clearances themselves (holders, speeds) are managed on
the Clearances tab here.

### Where clearances are hidden

A clearance has no page and appears in no directory, profile, chip, map
filter, API answer or connector: only the Clearances tab, the admin and the
console list them, and all three are superusers' alone. A member learns the
names of the clearances they hold on the Mana page's Regeneration tab and on
an object's "who reads this" control, and never of the others.

### Who gives clearances

Superusers, on the **Clearances** tab of this app (`communities/clearances/`,
2026-09-29 — beside Profiles and Communities in the strip, shown to
superusers only): every clearance with its holders and its mana refill
speeds; make one (at most seven), add and remove people, set the speeds,
remove a clearance nothing reads through any more (an app's PROTECT refuses
otherwise). What a clearance READS is each app's own business and stays in
that app (the wiki's page × clearance grid). Or in the Django admin, on the
clearance's own page (its **Members** field, written through
`clearance.members.set`, the relation's own door), or from the console
(`community_members join ada --clearance internal`). Never the membership
application: it names communities, and a community grants no reading.

## On the audit chain

Since 2026-09-28 every community and clearance operation is a record on the
platform's audit chain (`audit.py`, signals — so the admin, the membership
flow, the wiki's Clearances page and a shell are all covered): a community or a
clearance made, changed (the fields, before and after) or removed; a person
added to or taken out of a community (`MEMBER_ADDED`/`_REMOVED`) or given or
losing a clearance (`CLEARANCE_MEMBER_ADDED`/`_REMOVED`), from either side of
the relation and through a `clear()`; senior members; privileges; an application submitted and
each of its steps; a reference asked for, given (the applicant admitted) or
declined. Communities and clearances are recorded apart — the chain is where
a crossing of the two axes would show. Sign-ins, sign-outs and
accounts are recorded by `toto.audit.identity`. See `toto/audit/README.md`.

