"""The Capsule setting: one field every lab shares, and the rule for reading it.

The fact this suite exists to pin: a person may hold up to three Capsules, and
`require_capsule` refuses to guess between them. Before this setting, a second
mounted Capsule made the Python lab unusable — every start was refused as
ambiguous and there was no way to say which one. The setting is the answer,
and these tests are the shape of the answer.
"""

from __future__ import annotations

import json

from unittest import skipUnless

from django.apps import apps as django_apps
from django.test import override_settings
from django.urls import reverse

from toto.ambrosia import capsules, settings_spec
from toto.ambrosia.tests.base import AmbrosiaTestCase

#: **The compute tier this file is about may not be installed.** Compute
#: Capsules and the two language labs were parked on 2026-09-14
#: (zenobia/limbo/anastasia, .../dracena, .../texlab), and a test module that
#: imports a parked app at module scope does not skip — it breaks the whole
#: run with "doesn't declare an explicit app_label". So the imports are behind
#: this flag and every class below is skipped without them. On a host that
#: installs the tier again, this file runs exactly as it did.
HAS_CAPSULES = django_apps.is_installed("toto.anastasia")

if HAS_CAPSULES:
    from toto.anastasia import jobs
    from toto.anastasia import services as capsule_services
    from toto.anastasia.limits import Limits

POOL = {"cpu_millicores": 8000, "ram_mb": 16384, "scratch_mb": 8192,
        "pids": 4096}
SMALL = Limits(1000, 1024, 512, 256) if HAS_CAPSULES else None


@override_settings(
    ANASTASIA_POOL=POOL,
    ANASTASIA_MAX_GEARS_PER_USER=3,
    # Mounting talks to a runtime; the anastasia suite's fake stands in for
    # the manager here exactly as it does there.
    ANASTASIA_RUNTIME_BACKEND="toto.anastasia.tests.base.FakeRuntimeBackend",
)
@skipUnless(HAS_CAPSULES, "Compute Capsules are parked on this host")
class CapsuleSettingTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        from toto.anastasia.tests.base import FakeRuntimeBackend
        FakeRuntimeBackend.reset()
        self.ws = self.make_workspace(name="Room", kind="python")

    def _capsule(self, name, *, owner=None, mounted=True):
        lease = capsule_services.reserve(owner=owner or self.owner, name=name,
                                      limits=SMALL)
        if mounted:
            capsule_services.mount(lease=lease, actor=owner or self.owner)
        return lease

    def _fields(self):
        from toto.dracena import workspace_settings
        return workspace_settings.fields()

    def _capsule_field(self):
        return next(f for f in self._fields() if f.key == capsules.KEY)

    # -- the declaration ----------------------------------------------------

    def test_every_lab_declares_the_capsule_field_first(self):
        from toto.dracena import workspace_settings as py
        from toto.texlab import workspace_settings as tex

        for lab in (py, tex):
            with self.subTest(lab=lab.__name__):
                self.assertEqual(lab.fields()[0].key, capsules.KEY)
                self.assertTrue(lab.fields()[0].needs_execute,
                                "where a workspace runs is an execution decision")

    def test_the_choices_are_the_owners_capsules_and_automatic(self):
        a = self._capsule("alpha")
        b = self._capsule("beta", mounted=False)
        self._capsule("theirs", owner=self.other)        # somebody else's: absent

        options = dict(self._capsule_field().options(self.ws))
        self.assertIn(capsules.AUTOMATIC, options)
        self.assertIn(str(a.uuid), options)
        self.assertIn(str(b.uuid), options)
        self.assertEqual(len(options), 3, options)
        # An unmounted one is offered — you may choose before you mount — but
        # the label says so, because "choose it and nothing runs" needs a why.
        self.assertIn("mount it first", options[str(b.uuid)])
        self.assertNotIn("mount it first", options[str(a.uuid)])

    def test_describe_publishes_the_live_choices_to_the_panel(self):
        a = self._capsule("alpha")
        described = {d["key"]: d for d in settings_spec.describe(
            self._fields(), workspace=self.ws)}
        values = [c["value"] for c in described[capsules.KEY]["choices"]]
        self.assertEqual(values, [capsules.AUTOMATIC, str(a.uuid)])

    # -- saving -------------------------------------------------------------

    def _save(self, value):
        self.client.force_login(self.owner)
        url = reverse("dracena:workspace_settings", kwargs={"slug": self.ws.slug})
        response = self.client.post(url, data=json.dumps(
            {"settings": {capsules.KEY: value}}), content_type="application/json")
        # The view updated the ROW; this instance still holds the old JSON.
        # Without this, `preferred()` read the stale instance and three tests
        # below passed by resolving the one mounted Capsule automatically —
        # asserting the fix without ever exercising it.
        self.ws.refresh_from_db()
        return response

    def test_the_owner_can_choose_one_of_their_capsules(self):
        a = self._capsule("alpha")
        response = self._save(str(a.uuid))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["settings"][capsules.KEY], str(a.uuid))
        self.assertFalse(
            response.json()["restartRequired"],
            "nothing has to be restarted to move Capsules: every lab resolves "
            "one per job now, so the badge would tell people to restart a room "
            "that has nothing running in it")

    def test_somebody_elses_capsule_is_refused_by_name(self):
        theirs = self._capsule("theirs", owner=self.other)
        response = self._save(str(theirs.uuid))
        self.assertEqual(response.status_code, 409)
        self.assertIn(capsules.KEY, response.json()["fields"])

    def test_a_released_capsule_falls_back_to_automatic_on_the_way_out(self):
        """The same courtesy the ints get when a bound tightens.

        The stored uuid stays in the JSON; what must not happen is the panel
        showing a Capsule that no longer exists, or the run asking for it.
        """
        a = self._capsule("alpha")
        self._save(str(a.uuid))
        capsule_services.release(lease=a, actor=self.owner)

        values = settings_spec.effective(
            self._fields(), self.ws.settings_for("dracena"), workspace=self.ws)
        self.assertEqual(values[capsules.KEY], capsules.AUTOMATIC)
        self.assertIsNone(capsules.preferred(self.ws, "dracena"))

    # -- resolving ----------------------------------------------------------

    def test_two_mounted_capsules_resolve_once_the_workspace_says_which(self):
        """The bug, and its fix, in one test."""
        a = self._capsule("alpha")
        self._capsule("beta")

        with self.assertRaises(jobs.NoCapsule) as refused:
            capsules.resolve(self.ws, "dracena")
        self.assertIn("more than one", "; ".join(refused.exception.messages))

        self._save(str(a.uuid))
        self.assertEqual(capsules.resolve(self.ws, "dracena").pk, a.pk)

    def test_a_job_that_names_its_own_capsule_wins_over_the_setting(self):
        a = self._capsule("alpha")
        b = self._capsule("beta")
        self._save(str(a.uuid))
        chosen = capsules.resolve(self.ws, "dracena",
                               requested=(self.owner, str(b.uuid)))
        self.assertEqual(chosen.pk, b.pk)

    def test_the_setting_names_the_owners_capsule_even_for_a_collaborator(self):
        """The room runs on the owner's terms; the button-presser is metered.

        TWO owner Capsules, so automatic would refuse: the only way this
        resolves is by reading the setting and resolving it against the
        owner, not the requester — which is the rule. (With one Capsule this
        passed without ever reading the setting.)
        """
        a = self._capsule("alpha")
        self._capsule("beta")
        self._save(str(a.uuid))
        # The collaborator holds nothing; the workspace still resolves.
        chosen = capsules.resolve(self.ws, "dracena", requested=(self.other, None))
        self.assertEqual(chosen.pk, a.pk)

    def test_a_malformed_stored_value_reads_as_automatic_not_a_500(self):
        """Only an admin editing the JSON by hand can get one in; it must
        degrade the way effective() already does, not raise at query time."""
        a = self._capsule("alpha")
        self.ws.settings = {**(self.ws.settings or {}),
                            "dracena": {capsules.KEY: "not-a-uuid"}}
        self.ws.save(update_fields=["settings"])
        self.assertIsNone(capsules.preferred(self.ws, "dracena"))
        self.assertEqual(capsules.resolve(self.ws, "dracena").pk, a.pk)

    def test_no_lab_asks_for_a_restart_badge_any_more(self):
        """It is still the LAB'S call, and both labs now answer no.

        Python said yes until 2026-09-10 — a kernel moved Capsules only when it
        restarted — and LaTeX said no, because a compile resolves the Capsule
        afresh every time and there is no kernel to restart. Dracena works the
        LaTeX way now: each Run resolves a Capsule and exits.

        The badge MECHANISM is deliberately kept — `restart_hint` is still a
        field on `Field` and still rendered — because it is the right seam for
        a lab that does hold something between calls, which is what antaresia
        would be. What is asserted is that nobody claims it today, so a badge
        appearing again is a change somebody made rather than a leftover.
        """
        from toto.dracena import workspace_settings as py
        from toto.texlab import workspace_settings as tex

        for lab, fields in (("dracena", py.fields()), ("texlab", tex.fields())):
            for field in fields:
                with self.subTest(lab=lab, field=field.key):
                    self.assertFalse(
                        field.restart_hint,
                        f"{lab}.{field.key} badges a restart; there is nothing "
                        f"long-lived left to restart")

    def test_the_cap_is_three_concurrent_capsules(self):
        """Named here because it is why this whole setting exists."""
        from toto.anastasia import conf
        self.assertEqual(conf.max_capsules_per_user(), 3)
        for name in ("one", "two", "three"):
            self._capsule(name, mounted=False)
        with self.assertRaises(capsule_services.CapacityError):
            self._capsule("four", mounted=False)



class LegacySettingKeyTests(CapsuleSettingTests):
    """The stored key moved inside a JSON blob, where nothing type-checks it.

    `capsules.KEY` was "capsule" until 2026-09-10 and is "capsule" now. The value sits
    in `Workspace.settings`, so a plain rename raises nothing at all — it just
    makes every workspace that had chosen a capsule read as AUTOMATIC.

    That is the dangerous shape: AUTOMATIC on an account with two mounted
    capsules is exactly the ambiguity `require_capsule` refuses, so the symptom is
    "this job needs you to say which one to use" appearing on a workspace that
    already said, with nothing pointing at a rename as the cause.

    Inherits `CapsuleSettingTests` for its pool settings and fake runtime; these
    are about the key, not about the field.
    """

    def _pin(self, key, value):
        self.ws.settings = {"dracena": {key: str(value)}}
        self.ws.save(update_fields=["settings"])

    def test_a_row_written_before_the_rename_is_still_honoured(self):
        lease = self._capsule("old")
        self._pin(capsules.LEGACY_KEY, lease.uuid)
        self.assertEqual(capsules.preferred(self.ws, "dracena"), str(lease.uuid))

    def test_a_row_written_after_the_rename_is_honoured(self):
        lease = self._capsule("new")
        self._pin(capsules.KEY, lease.uuid)
        self.assertEqual(capsules.preferred(self.ws, "dracena"), str(lease.uuid))

    def test_the_new_key_wins_when_both_are_present(self):
        """A deployment can write the new key before the migration moves the
        old one, so both coexist for a while. What the current code wrote is
        what the user last chose."""
        old_lease = self._capsule("first")
        new_lease = self._capsule("second")
        self.ws.settings = {"dracena": {capsules.LEGACY_KEY: str(old_lease.uuid),
                                        capsules.KEY: str(new_lease.uuid)}}
        self.ws.save(update_fields=["settings"])
        self.assertEqual(capsules.preferred(self.ws, "dracena"),
                         str(new_lease.uuid))

    def test_the_migration_moves_the_key_and_keeps_the_value(self):
        """The migration is a RunPython over free-form JSON, so nothing but a
        test can tell you it works."""
        import importlib

        module = importlib.import_module(
            "toto.ambrosia.migrations.0005_capsule_setting_key")
        lease = self._capsule("pinned")
        self._pin(capsules.LEGACY_KEY, lease.uuid)

        from toto.ambrosia.models import Workspace

        class _Apps:
            @staticmethod
            def get_model(app_label, model_name):
                return Workspace

        module.to_capsule(_Apps, None)

        self.ws.refresh_from_db()
        section = self.ws.settings["dracena"]
        self.assertEqual(section.get(capsules.KEY), str(lease.uuid))
        self.assertNotIn(capsules.LEGACY_KEY, section,
                         "the old key must go, or the fallback keeps it alive")

    def test_a_workspace_that_never_chose_is_untouched(self):
        """Most rows have no such key. The migration must not rewrite them —
        that is the difference between a quick migration and a timeout."""
        import importlib

        module = importlib.import_module(
            "toto.ambrosia.migrations.0005_capsule_setting_key")
        self.ws.settings = {"dracena": {"exec_timeout": 30}}
        self.ws.save(update_fields=["settings"])

        from toto.ambrosia.models import Workspace

        class _Apps:
            @staticmethod
            def get_model(app_label, model_name):
                return Workspace

        module.to_capsule(_Apps, None)
        self.ws.refresh_from_db()
        self.assertEqual(self.ws.settings, {"dracena": {"exec_timeout": 30}})
