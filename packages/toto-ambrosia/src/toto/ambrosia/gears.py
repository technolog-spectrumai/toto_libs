"""Which Compute Gear a workspace runs in — one setting, shared by every lab.

Why it is a WORKSPACE setting and not a per-job prompt: a person may hold up to
three Gears at once (``ANASTASIA_MAX_GEARS_PER_USER``), and
``toto.anastasia.jobs.require_gear`` refuses to guess between them — "you have
more than one mounted Gear, so this job needs you to say which one". Without
this field a second Gear made the Python lab unusable: the kernel start never
named one, so it was refused the moment a second existed. A workspace that
says where it runs is the answer, and it is the same answer for every lab, so
it is declared once here and each lab appends it to its own fields.

Whose Gear: the OWNER's. The setting is edited by the owner (the settings
route is for_edit), the options are the owner's reservations, and at run time
the job is placed in the owner's Gear — including a job a collaborator starts.
That matches what a workspace already is: a room in the owner's storage, run
on the owner's terms. Metering is untouched by this — a compile or an execute
is still charged to the person who pressed the button.

The base knows nothing of Gears (``settings_spec`` carries a generic
``choices_for``); this module is the one place in ambrosia that does, and it
imports anastasia lazily and only behind ``apps.is_installed`` so a build
without Compute Gears has no field, no import and no behaviour change.
"""

from __future__ import annotations

from django.apps import apps
from django.utils.translation import gettext_lazy as _

from .settings_spec import CHOICE, Field

#: The setting's key in every lab's stored settings. Written under this name.
KEY = "capsule"

#: What it was called before 2026-09-10. READ, NEVER WRITTEN.
#:
#: The value lives in a JSONField on live workspace rows, so a plain rename
#: would not error — it would make `preferred()` find nothing and fall back to
#: AUTOMATIC for every workspace that had chosen a capsule. That is worse than
#: an error: on an account holding two mounted capsules, "automatic" is exactly
#: the ambiguity `require_gear` refuses, so every job would start failing with
#: "say which one to use" and nothing would point at a rename as the cause.
#:
#: `0005_capsule_setting_key` rewrites the stored blobs. This fallback covers
#: the gap between deploying the code and running the migration, and rows the
#: migration could not reach. Delete both once neither can matter.
LEGACY_KEY = "gear"

#: The stored value for "no preference" — resolve the way the lab always did.
AUTOMATIC = ""


def available() -> bool:
    return apps.is_installed("toto.anastasia")


def _choices(workspace) -> tuple[tuple[str, str], ...]:
    """Automatic, then every OPEN reservation of the owner, newest first.

    Open rather than only mounted: a person may pick the Gear they intend to
    mount for this room before mounting it, and the label says where each one
    stands so the choice is informed. Resolution at run time still demands a
    mounted one — that is ``require_gear``'s job, not this list's.
    """
    from toto.anastasia import jobs

    options = [(AUTOMATIC, _("Automatic (your only mounted Gear)"))]
    for option in jobs.gear_options(workspace.owner):
        label = "%s — %s" % (option["name"], option["state"].lower())
        if not option["ready"]:
            label = "%s (%s)" % (label, _("mount it first"))
        options.append((option["value"], label))
    return tuple(options)


def field(*, restart_hint: bool = False) -> Field | None:
    """The declaration a lab appends to its fields, or None with no Gears.

    ``restart_hint`` is the LAB's to say, not this module's. A Python
    workspace moves Gears only when its kernel restarts, so the panel should
    badge the field and say so after a save; a LaTeX workspace resolves the
    Gear afresh on every compile and has no kernel — the same badge there
    told people to "restart the kernel" of a room that has none.
    """
    if not available():
        return None
    return Field(
        key=KEY,
        kind=CHOICE,
        label=_("Compute Gear"),
        default=AUTOMATIC,
        choices_for=_choices,
        needs_execute=True,
        restart_hint=restart_hint,
        help_text=_("Which of your Compute Gears this workspace runs in. "
                    "Automatic works while you hold exactly one mounted Gear; "
                    "with more than one you have to choose, or every job is "
                    "refused as ambiguous. Jobs started here by collaborators "
                    "run in this Gear too."),
    )


def preferred(workspace, namespace: str) -> str | None:
    """The Gear uuid this workspace asks for, or None for automatic.

    Validated against what the owner holds NOW, not what they held when the
    setting was saved: a released Gear's uuid stays in the stored JSON until
    the next save, and handing a dead uuid to ``require_gear`` would turn
    "you released that Gear" into a refusal that reads like a bug.
    """
    if not available():
        return None
    section = workspace.settings_for(namespace)
    stored = section.get(KEY) or section.get(LEGACY_KEY) or AUTOMATIC
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
    """The Gear a job in this workspace runs in, or a ``jobs.NoGear`` refusal.

    Precedence: a Gear named explicitly for this one job beats the workspace
    setting, which beats automatic. An explicit name is resolved against the
    REQUESTER — it is their request and their Gear; the setting is resolved
    against the owner, because it names one of the owner's reservations.

    ``requested`` is (user, uuid) when a caller names a Gear for this job.
    No page in the product does so today — the compile endpoint accepts a
    form-encoded ``gear`` and the room's Compile button sends none — so in
    practice the setting decides. The path is kept and tested because it is
    the right seam for a future per-job picker, and because it is where the
    requester's-own-Gear rule lives.
    """
    from toto.anastasia import jobs

    if requested and requested[1]:
        user, uuid = requested
        return jobs.require_gear(user, uuid)
    return jobs.require_gear(workspace.owner, preferred(workspace, namespace))
