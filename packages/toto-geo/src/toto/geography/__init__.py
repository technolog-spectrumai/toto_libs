"""Geography (2026-10-06): two stored types and two searches.

A point with its address (``Address``) and a zone (``Zone``, one closed
ring) are the only rows with a geometry. A person may have one point
(``PersonAddress``), a community one headquarters point and one zone
(``CommunityHeadquarters``). Place names are found through the host's
geocoder (``places``) and routes through its router (``routing``); both are
charged compute mana, and a route is calculated, answered and forgotten.

This app is not ``toto.locations``, the parked map beside it in this wheel:
no stored routes, territories, layers or map domains, and nothing here
imports it. A host installs one of the two, or both; they share no table, no
URL, no template and no static path.
"""
