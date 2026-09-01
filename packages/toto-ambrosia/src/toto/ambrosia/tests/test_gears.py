"""The Gear setting: one field every lab shares, and the rule for reading it.

The fact this suite exists to pin: a person may hold up to three Gears, and
`require_gear` refuses to guess between them. Before this setting, a second
mounted Gear made the Python lab unusable — every start was refused as
ambiguous and there was no way to say which one. The setting is the answer,
and these tests are the shape of the answer.
"""

from __future__ import annotations

import json

from django.test import override_settings
from django.urls import reverse

from toto.ambrosia import gears, settings_spec
from toto.ambrosia.tests.base import AmbrosiaTestCase
from toto.anastasia import jobs
from toto.anastasia import services as gear_services
from toto.anastasia.limits import Limits

POOL = {"cpu_millicores": 8000, "ram_mb": 16384, "scratch_mb": 8192,
        "pids": 4096}
SMALL = Limits(1000, 1024, 512, 256)


@override_settings(
    ANASTASIA_POOL=POOL,
    ANASTASIA_MAX_GEARS_PER_USER=3,
    # Mounting talks to a runtime; the anastasia suite's fake stands in for
    # the manager here exactly as it does there.
    ANASTASIA_RUNTIME_BACKEND="toto.anastasia.tests.base.FakeRuntimeBackend",
)
class GearSettingTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        from toto.anastasia.tests.base import FakeRuntimeBackend
        FakeRuntimeBackend.reset()
        self.ws = self.make_workspace(name="Room", kind="python")

    def _gear(self, name, *, owner=None, mounted=True):
        lease = gear_services.reserve(owner=owner or self.owner, name=name,
                                      limits=SMALL)
        if mounted:
            gear_services.mount(lease=lease, actor=owner or self.owner)
        return lease

    def _fields(self):
        from toto.dracena import workspace_settings
        return workspace_settings.fields()

    def _gear_field(self):
        return next(f for f in self._fields() if f.key == gears.KEY)

    # -- the declaration ----------------------------------------------------

    def test_every_lab_declares_the_gear_field_first(self):
        from toto.dracena import workspace_settings as py
        from toto.texlab import workspace_settings as tex

        for lab in (py, tex):
            with self.subTest(lab=lab.__name__):
                self.assertEqual(lab.fields()[0].key, gears.KEY)
                self.assertTrue(lab.fields()[0].needs_execute,
                                "where a workspace runs is an execution decision")

    def test_the_choices_are_the_owners_gears_and_automatic(self):
        a = self._gear("alpha")
        b = self._gear("beta", mounted=False)
        self._gear("theirs", owner=self.other)        # somebody else's: absent

        options = dict(self._gear_field().options(self.ws))
        self.assertIn(gears.AUTOMATIC, options)
        self.assertIn(str(a.uuid), options)
        self.assertIn(str(b.uuid), options)
        self.assertEqual(len(options), 3, options)
        # An unmounted one is offered — you may choose before you mount — but
        # the label says so, because "choose it and nothing runs" needs a why.
        self.assertIn("mount it first", options[str(b.uuid)])
        self.assertNotIn("mount it first", options[str(a.uuid)])

    def test_describe_publishes_the_live_choices_to_the_panel(self):
        a = self._gear("alpha")
        described = {d["key"]: d for d in settings_spec.describe(
            self._fields(), workspace=self.ws)}
        values = [c["value"] for c in described[gears.KEY]["choices"]]
        self.assertEqual(values, [gears.AUTOMATIC, str(a.uuid)])

    # -- saving -------------------------------------------------------------

    def _save(self, value):
        self.client.force_login(self.owner)
        url = reverse("dracena:workspace_settings", kwargs={"slug": self.ws.slug})
        response = self.client.post(url, data=json.dumps(
            {"settings": {gears.KEY: value}}), content_type="application/json")
        # The view updated the ROW; this instance still holds the old JSON.
        # Without this, `preferred()` read the stale instance and three tests
        # below passed by resolving the one mounted Gear automatically —
        # asserting the fix without ever exercising it.
        self.ws.refresh_from_db()
        return response

    def test_the_owner_can_choose_one_of_their_gears(self):
        a = self._gear("alpha")
        response = self._save(str(a.uuid))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["settings"][gears.KEY], str(a.uuid))
        self.assertTrue(response.json()["restartRequired"],
                        "a kernel moves Gears only when it restarts")

    def test_somebody_elses_gear_is_refused_by_name(self):
        theirs = self._gear("theirs", owner=self.other)
        response = self._save(str(theirs.uuid))
        self.assertEqual(response.status_code, 409)
        self.assertIn(gears.KEY, response.json()["fields"])

    def test_a_released_gear_falls_back_to_automatic_on_the_way_out(self):
        """The same courtesy the ints get when a bound tightens.

        The stored uuid stays in the JSON; what must not happen is the panel
        showing a Gear that no longer exists, or the run asking for it.
        """
        a = self._gear("alpha")
        self._save(str(a.uuid))
        gear_services.release(lease=a, actor=self.owner)

        values = settings_spec.effective(
            self._fields(), self.ws.settings_for("dracena"), workspace=self.ws)
        self.assertEqual(values[gears.KEY], gears.AUTOMATIC)
        self.assertIsNone(gears.preferred(self.ws, "dracena"))

    # -- resolving ----------------------------------------------------------

    def test_two_mounted_gears_resolve_once_the_workspace_says_which(self):
        """The bug, and its fix, in one test."""
        a = self._gear("alpha")
        self._gear("beta")

        with self.assertRaises(jobs.NoGear) as refused:
            gears.resolve(self.ws, "dracena")
        self.assertIn("more than one", "; ".join(refused.exception.messages))

        self._save(str(a.uuid))
        self.assertEqual(gears.resolve(self.ws, "dracena").pk, a.pk)

    def test_a_job_that_names_its_own_gear_wins_over_the_setting(self):
        a = self._gear("alpha")
        b = self._gear("beta")
        self._save(str(a.uuid))
        chosen = gears.resolve(self.ws, "dracena",
                               requested=(self.owner, str(b.uuid)))
        self.assertEqual(chosen.pk, b.pk)

    def test_the_setting_names_the_owners_gear_even_for_a_collaborator(self):
        """The room runs on the owner's terms; the button-presser is metered.

        TWO owner Gears, so automatic would refuse: the only way this
        resolves is by reading the setting and resolving it against the
        owner, not the requester — which is the rule. (With one Gear this
        passed without ever reading the setting.)
        """
        a = self._gear("alpha")
        self._gear("beta")
        self._save(str(a.uuid))
        # The collaborator holds nothing; the workspace still resolves.
        chosen = gears.resolve(self.ws, "dracena", requested=(self.other, None))
        self.assertEqual(chosen.pk, a.pk)

    def test_a_malformed_stored_value_reads_as_automatic_not_a_500(self):
        """Only an admin editing the JSON by hand can get one in; it must
        degrade the way effective() already does, not raise at query time."""
        a = self._gear("alpha")
        self.ws.settings = {**(self.ws.settings or {}),
                            "dracena": {gears.KEY: "not-a-uuid"}}
        self.ws.save(update_fields=["settings"])
        self.assertIsNone(gears.preferred(self.ws, "dracena"))
        self.assertEqual(gears.resolve(self.ws, "dracena").pk, a.pk)

    def test_the_restart_badge_is_the_labs_call(self):
        """A LaTeX room has no kernel to restart."""
        from toto.dracena import workspace_settings as py
        from toto.texlab import workspace_settings as tex
        self.assertTrue(py.fields()[0].restart_hint)
        self.assertFalse(tex.fields()[0].restart_hint)

    def test_the_cap_is_three_concurrent_gears(self):
        """Named here because it is why this whole setting exists."""
        from toto.anastasia import conf
        self.assertEqual(conf.max_gears_per_user(), 3)
        for name in ("one", "two", "three"):
            self._gear(name, mounted=False)
        with self.assertRaises(gear_services.CapacityError):
            self._gear("four", mounted=False)
