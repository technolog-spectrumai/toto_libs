# toto.datalink — parked 2026-08-15

Row replication between toto instances: seven models, a policy registry, a
canonical wire format, a three-way merge, and a two-database test harness.
What it never had was a transport — `urlpatterns` was an empty list with a
comment promising "later steps of the build", `peer_views` and the federation
bridge were cited but unwritten, and no host ever installed the app (no
`BUILD_DATALINK` exists in any settings or profile).

Parked by the sealing decision of the Remote Buckets campaign: **the vault's
bucket peer API is the platform's one host-to-host data channel.** Identity
federation (SSO) and the ledger bridge (clearing) keep their own narrow,
shipped channels — they move logins and value, not data. A second, general
row-replication channel was judged to be surface without a user: everything a
peer was going to read through it is either refused by its own policies
(credentials, files, ACLs) or better served as a bucket.

The per-app `datalink_policies.py` registrations (api, backup, core, events,
gervazy, jess, locations, people, socialhub, vault) were deleted with the
engine — they were pure data for a registry that no longer loads. The two
doctrines the vault's policy file carried live on where the bucket link
enforces them: the fail-open directory-whitelist trap (why per-user ACLs never
cross hosts) and the encrypted-non-portable rule (Fernet under an
instance-local Argon2id salt — ciphertext moved to another host is permanently
unopenable, so transfers refuse encrypted non-PDFs).

The credential mechanics designed here — the directional grant/peer split,
the magic-token-before-PBKDF2 cheap check, the no-`uid`-field backup guard —
were not lost: `toto.vault.peering` copies them, with attribution, for
`BucketGrant`/`BucketPeer`.

If row replication is ever wanted again, this directory is the starting
point, not a template to rewrite. It stays parked.
