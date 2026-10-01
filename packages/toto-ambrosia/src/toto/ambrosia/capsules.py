"""Which Compute Capsule a workspace runs in — one setting, shared by every lab.

Why it is a WORKSPACE setting and not a per-job prompt: a person may hold up to
three Capsules at once (``ANASTASIA_MAX_GEARS_PER_USER``), and
``toto.anastasia.jobs.require_capsule`` refuses to guess between them — "you have
more than one mounted Capsule, so this job needs you to say which one". Without
this field a second Capsule made the Python lab unusable: the kernel start never
named one, so it was refused the moment a second existed. A workspace that
says where it runs is the answer, and it is the same answer for every lab, so
it is declared once here and each lab appends it to its own fields.

Whose Capsule: the OWNER's. The setting is edited by the owner (the settings
route is for_edit), the options are the owner's reservations, and at run time
the job is placed in the owner's Capsule — including a job a collaborator starts.
That matches what a workspace already is: a room in the owner's storage, run
on the owner's terms. Metering is untouched by this — a compile or an execute
is still charged to the person who pressed the button.

The base knows nothing of Capsules (``settings_spec`` carries a generic
``choices_for``); this module is the one place in ambrosia that does, and it
imports anastasia lazily and only behind ``apps.is_installed`` so a build
without Compute Capsules has no field, no import and no behaviour change.
"""

from __future__ import annotations

from django.apps import apps
from django.utils.translation import gettext_lazy as _

from .settings_spec import CHOICE, Field

#: The setting's key in every lab's stored settings. Written under this name.
KEY = "capsule"

#: The stored value for "no preference" — resolve the way the lab always did.
AUTOMATIC = ""


def available() -> bool:
    return apps.is_installed("toto.anastasia")


def _choices(workspace) -> tuple[tuple[str, str], ...]:
    """Automatic, then every OPEN reservation of the owner, newest first.

    Open rather than only mounted: a person may pick the Capsule they intend to
    mount for this room before mounting it, and the label says where each one
    stands so the choice is informed. Resolution at run time still demands a
    mounted one — that is ``require_capsule``'s job, not this list's.
    """
    from toto.anastasia import jobs

    options = [(AUTOMATIC, _("Automatic (your only mounted Capsule)"))]
    for option in jobs.capsule_options(workspace.owner):
        label = "%s — %s" % (option["name"], option["state"].lower())
        if not option["ready"]:
            label = "%s (%s)" % (label, _("mount it first"))
        options.append((option["value"], label))
    return tuple(options)


def field(*, restart_hint: bool = False) -> Field | None:
    """The declaration a lab appends to its fields, or None with no Capsules.

    ``restart_hint`` is the LAB's to say, not this module's — and since
    2026-09-10 no lab says yes.

    A Python workspace used to: it moved Capsules only when its kernel
    restarted, so the panel badged the field and said so after a save. A LaTeX
    workspace never did, because a compile resolves the Capsule afresh every
    time and there is no kernel — the same badge there told people to "restart
    the kernel" of a room that has none. Dracena's kernel is gone and its Runs
    resolve a Capsule per job, so it is now in exactly LaTeX's position.

    The parameter stays because the question is still a real one: a lab that
    holds something between calls would want the badge, and the honest place to
    decide it is the lab. False is the answer for anything that resolves per
    job, which is everything today.
    """
    if not available():
        return None
    return Field(
        key=KEY,
        kind=CHOICE,
        label=_("Compute Capsule"),
        default=AUTOMATIC,
        choices_for=_choices,
        needs_execute=True,
        restart_hint=restart_hint,
        help_text=_("Which of your Compute Capsules this workspace runs in. "
                    "Automatic works while you hold exactly one mounted Capsule; "
                    "with more than one you have to choose, or every job is "
                    "refused as ambiguous. Jobs started here by collaborators "
                    "run in this Capsule too."),
    )


def preferred(workspace, namespace: str) -> str | None:
    """The Capsule uuid this workspace asks for, or None for automatic.

    Validated against what the owner holds NOW, not what they held when the
    setting was saved: a released Capsule's uuid stays in the stored JSON until
    the next save, and handing a dead uuid to ``require_capsule`` would turn
    "you released that Capsule" into a refusal that reads like a bug.
    """
    if not available():
        return None
    section = workspace.settings_for(namespace)
    stored = section.get(KEY) or AUTOMATIC
    if stored == AUTOMATIC:
        return None
    # A value that is not a uuid at all cannot have come through clean() —
    # only an admin editing the JSON by hand gets one in — but a UUIDField
    # lookup on it raises ValidationError at query-build time, which is a 500
    # on every compile and start while the panel calmly shows "Automatic".
    # Treat it the way effective() does: as nothing.
    import uuid as uuid_module

    try:
        uuid_module.UUID(str(stored))
    except (TypeError, ValueError):
        return None
    from toto.anastasia.models import ComputeLease

    if ComputeLease.objects.open().filter(
            owner=workspace.owner, uuid=stored).exists():
        return stored
    return None


def resolve(workspace, namespace: str, *, requested=None):
    """The Capsule a job in this workspace runs in, or a ``jobs.NoCapsule`` refusal.

    Precedence: a Capsule named explicitly for this one job beats the workspace
    setting, which beats automatic. An explicit name is resolved against the
    REQUESTER — it is their request and their Capsule; the setting is resolved
    against the owner, because it names one of the owner's reservations.

    ``requested`` is (user, uuid) when a caller names a Capsule for this job.
    No page in the product does so today — the compile endpoint accepts a
    form-encoded ``capsule`` and the room's Compile button sends none — so in
    practice the setting decides. The path is kept and tested because it is
    the right seam for a future per-job picker, and because it is where the
    requester's-own-Capsule rule lives.
    """
    from toto.anastasia import jobs

    if requested and requested[1]:
        user, uuid = requested
        return jobs.require_capsule(user, uuid)
    return jobs.require_capsule(workspace.owner, preferred(workspace, namespace))
