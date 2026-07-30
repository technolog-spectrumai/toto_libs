"""people.Person — the user-visible identity, replicated WITHOUT its account.

This is the app where the "datalink never touches users" rule shows its shape. A
Person travels; the `auth.User` it may be attached to does not. `Person.user` is
nullable, so a Person arrives unclaimed, and
``sso_client._link_person`` binds it to a local account on that person's first
federated sign-in — matching on `federated_sub` if it has one, otherwise adopting an
unclaimed Person **by slug**.

Two consequences follow from that adoption path and both are load-bearing:

* **`slug` must travel verbatim.** ``Person.save()`` invents `ada-1` when a slug
  collides, so a receiver that let save() re-derive it would hand federation a slug
  that matches nobody, permanently breaking adoption for that person. The engine
  writes the slug explicitly, and a collision against a *different* person is a
  reviewable conflict, never a rename.
* **`federated_sub` must NOT travel.** It is the receiver's own binding to its own
  identity provider. Copying the peer's subject would make `_link_person` adopt the
  wrong Person on the next sign-in. Its own help_text notes it is empty on a provider.
"""
from toto.datalink.registry import (
    FK_NULL,
    IDENTITY_UID,
    STAGE_MEMBERSHIP,
    STAGE_PEOPLE,
    SyncPolicy,
    register,
)

register(SyncPolicy(
    "people.Person", stage=STAGE_PEOPLE, identity=IDENTITY_UID,
    unique_guards=(("slug",),),
    fields=(
        "display_name", "bio", "joined_date", "slug", "date_of_birth",
        "address", "email", "phone", "is_federal_agent", "digital_signature",
        "preferred_language", "patron",
    ),
    m2m=("communities",), m2m_stage=STAGE_MEMBERSHIP,
    parent_field="patron",
    # people has no auto_now field, so a both-changed conflict on a Person is
    # reported to the operator rather than resolved by a timestamp. That is the
    # point of the merge base.
    timestamp_field=None,
    refs={"user": FK_NULL},
    notes=(
        "`user` is ABSENT FROM `fields`, which is stronger than FK_NULL and is the "
        "distinction that matters: FK_NULL would emit the field and write NULL, so an "
        "update would detach an already-claimed Person from its account on every run. "
        "Being absent means the writer's update_fields never names it, so a claimed "
        "Person keeps its account and an unclaimed one stays NULL. The refs entry is "
        "documentation — it makes the drop show up in describe_registry. "
        "`avatar` is omitted (an ImageField; no file bytes travel). `federated_sub` is "
        "omitted deliberately: see the module docstring. `uid` travels as the identity, "
        "not as a field."
    ),
))
