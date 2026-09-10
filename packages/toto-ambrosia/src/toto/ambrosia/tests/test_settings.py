"""The settings endpoint, the clamp, and the drawer in the room.

The clamp tests must never skip: the whole point of storing a setting on the
workspace is that it works on a host with no ledger, which is this one. The
billed shape is reached by mocking the grant instead.
"""

import unittest

import json
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from toto.ambrosia import limits, settings_spec
from toto.ambrosia.models import Workspace
from toto.ambrosia.tests.base import AmbrosiaTestCase





#: The dial these clamp tests are written against — DECLARED HERE, on purpose.
#:
#: This file was written against `dracena.kernel_idle`, which was deleted on
#: 2026-09-10 when the Python kernel went, and every number below was a literal
#: (3600, 7200, 604800) tied to that declaration. Retiring the dial would have
#: turned real assertions into assertions about a key `times` does not know —
#: where `free_seconds` answers 0 rather than raising, so the class keeps
#: passing while proving nothing.
#:
#: Repointing at another app's dial only moves the problem: texlab can be
#: retired too. What is under test is AMBROSIA'S OWN RULE — "cap always, charge
#: where there is a ledger", `limits.py` — so the subject belongs to this file.
#: `_use_the_test_dial` registers it per test and removes it afterwards, the
#: same way the registry entry is swapped elsewhere in this suite.
DIAL = "ambrosia.test_dial"
FREE, CEILING = 3600, 604800


def _free() -> int:
    from toto.quota import times

    return times.free_seconds(DIAL)


def _ceiling() -> int:
    """How far `DIAL` may be turned up ON THIS HOST.

    See toto.ambrosia.limits: with a ledger installed a paid grant binds the
    range, so an ungranted workspace cannot exceed the free default; without
    one the dial's declaration binds.
    """
    from django.apps import apps

    from toto.quota import times

    if apps.is_installed("toto.tax"):
        return times.free_seconds(DIAL)
    return times.ceiling_seconds(DIAL)


class SettingsEndpointTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace(name="Configured", kind="python")

    def _url(self, slug=None):
        return reverse("dracena:workspace_settings",
                       kwargs={"slug": slug or self.ws.slug})

    def _post(self, payload):
        return self.client.post(self._url(), data=json.dumps(payload),
                                content_type="application/json")

    def test_the_owner_can_save_a_setting(self):
        self.client.force_login(self.owner)
        response = self._post({"settings": {"exec_timeout": 45}})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["settings"]["exec_timeout"], 45)

        self.ws.refresh_from_db()
        self.assertEqual(self.ws.settings_for("dracena")["exec_timeout"], 45)

    def test_settings_survive_a_reload(self):
        # Posted two keys until 2026-09-10; `inline_plots` is deleted with the
        # kernel that ran %matplotlib, and dracena declares one field now.
        self.client.force_login(self.owner)
        self._post({"settings": {"exec_timeout": 45}})

        fresh = Workspace.objects.get(pk=self.ws.pk)
        self.assertEqual(fresh.settings_for("dracena")["exec_timeout"], 45)

    def test_a_stranger_gets_404_not_403(self):
        # Same reasoning as every other mutation here: 403 would confirm the
        # workspace exists.
        self.client.force_login(self.other)
        self.assertEqual(self._post({"settings": {"exec_timeout": 45}}).status_code, 404)

    def test_staff_looking_at_someone_elses_workspace_cannot_save(self):
        theirs = self.make_workspace(owner=self.other, name="Theirs", kind="python")
        self.client.force_login(self.admin)
        response = self.client.post(
            self._url(theirs.slug), data=json.dumps({"settings": {"exec_timeout": 45}}),
            content_type="application/json")
        self.assertEqual(response.status_code, 404)

    def test_an_unknown_key_is_refused_rather_than_dropped(self):
        # A typo'd setting that reads as saved and does nothing is the worst
        # outcome available.
        self.client.force_login(self.owner)
        response = self._post({"settings": {"exec_timeoot": 45}})
        self.assertEqual(response.status_code, 409)
        self.assertIn("exec_timeoot", response.json()["error"])

    def test_a_value_out_of_bounds_names_the_field(self):
        self.client.force_login(self.owner)
        response = self._post({"settings": {"exec_timeout": 99999}})
        self.assertEqual(response.status_code, 409)
        self.assertIn("exec_timeout", response.json()["fields"])

    def test_a_value_that_is_not_a_number_is_refused(self):
        self.client.force_login(self.owner)
        response = self._post({"settings": {"exec_timeout": "soon"}})
        self.assertEqual(response.status_code, 409)

    @override_settings(AMBROSIA_EXECUTION_ACCESS="superuser")
    def test_an_execution_knob_is_refused_without_the_right_to_run(self):
        # The owner is staff, not a superuser: they may edit their files and
        # not decide what their interpreter may do.
        with mock.patch("toto.ambrosia.permissions.EXECUTION_ACCESS", "superuser"):
            self.client.force_login(self.owner)
            response = self._post({"settings": {"exec_timeout": 45}})
        self.assertEqual(response.status_code, 409)
        self.assertIn("exec_timeout", response.json()["fields"])

    def test_reset_drops_every_override(self):
        self.client.force_login(self.owner)
        self._post({"settings": {"exec_timeout": 45}})
        response = self._post({"reset": True})

        self.assertTrue(response.json()["ok"])
        self.ws.refresh_from_db()
        self.assertEqual(self.ws.settings_for("dracena"), {})

    def test_get_is_refused(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self._url()).status_code, 405)

    def test_what_the_room_publishes_can_be_posted_straight_back(self):
        # The panel is handed `settings` and posts them back. Optional fields
        # are published as null, so if null were not accepted the drawer could
        # never save anything on an untouched workspace.
        self.client.force_login(self.owner)
        room = self.client.get(
            reverse("dracena:workspace", kwargs={"slug": self.ws.slug}))
        published = json.loads(
            room.content.decode().split('id="ambrosia-config"')[1]
            .split(">", 1)[1].split("</script>")[0])["settings"]

        response = self._post({"settings": published})
        self.assertEqual(response.status_code, 200, response.content)

    def test_null_means_use_the_host_default_not_freeze_it(self):
        # Storing the current default would silently detach this workspace
        # from a later change to the host setting.
        self.client.force_login(self.owner)
        self._post({"settings": {"exec_timeout": 45}})
        self._post({"settings": {"exec_timeout": None}})

        self.ws.refresh_from_db()
        self.assertNotIn("exec_timeout", self.ws.settings_for("dracena"))

    def test_a_list_payload_is_refused_rather_than_crashing(self):
        self.client.force_login(self.owner)
        response = self._post({"settings": ["exec_timeout"]})
        self.assertEqual(response.status_code, 409)


class ClampTests(AmbrosiaTestCase):
    """The rule from limits.py: cap always, charge where there is a ledger."""

    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace(name="Clamped", kind="python")
        self._use_the_test_dial()

    def _use_the_test_dial(self):
        """Register `DIAL` for the duration of one test, then take it away.

        The registry has no `unregister` — it is populated once at startup and
        never edited — so the entry is removed by hand on cleanup. Poking
        `_limits` is the same liberty `PartialSnapshotTests` takes with
        `registry._BY_KIND`: a test that needs a hook swapped has to reach for
        the dict, and doing it under `addCleanup` is what keeps it contained.
        """
        from toto.quota import times

        limit = times.TimeLimit(
            key=DIAL, label="Clamp test dial", app_label="ambrosia",
            scope="workspace", scope_model="ambrosia.Workspace",
            scope_owner_attr="owner_id",
            free_seconds=FREE, ceiling_seconds=CEILING,
            description="Declared by toto.ambrosia's own tests.")
        # ORDER MATTERS: addCleanup is LIFO, so the leak check has to be
        # registered FIRST to run LAST — after the pop. Registered the other
        # way round it fires while the dial is still there and fails every
        # test in the class, which is exactly what happened when this was
        # written as a test of its own.
        self.addCleanup(
            lambda: self.assertIsNone(
                times.registry.get(DIAL),
                "the clamp test dial outlived its test — every other suite, "
                "and any page listing a user's dials, would now see it"))
        self.addCleanup(times.registry._limits.pop, DIAL, None)
        times.registry._limits[DIAL] = limit

    def test_the_dial_under_test_exists(self):
        """THE GUARD ON EVERY OTHER TEST IN THIS CLASS.

        `times.free_seconds` and `ceiling_seconds` degrade to 0 on an unknown
        key rather than raising — deliberately, so a missing dial never 500s a
        page. That same kindness means this whole class would clamp everything
        to [0, 0] and still pass if `DIAL` named nothing. First.
        """
        from toto.quota import times

        self.assertIsNotNone(times.registry.get(DIAL),
                             f"{DIAL} is not registered, so every clamp test "
                             f"below is asserting against zeroes")
        self.assertEqual(_free(), FREE)
        self.assertGreater(times.ceiling_seconds(DIAL), _free())


    def test_how_far_the_knob_turns_depends_on_whether_there_is_a_ledger(self):
        """"Cap always, charge where there is a ledger" — both halves.

        With no toto.tax the dial's DECLARED ceiling is the cap, because there
        is nothing to sell the range with. With one, an ungranted workspace
        sits at the free default and buying is what raises it. This test moved
        from a ledger-free host to one with a ledger, which is why it asserts
        the rule rather than a number.
        """
        low, high = limits.allowed_range(DIAL, self.ws)
        self.assertEqual(low, _free())      # free — the floor, always
        self.assertEqual(high, _ceiling())

    def test_nothing_stored_means_the_entitlement_unchanged(self):
        # An untouched workspace behaves exactly as it did before the feature.
        self.assertEqual(limits.resolve_seconds(DIAL, self.ws, None), _free())

    def test_a_stored_value_is_clamped_at_both_ends(self):
        self.assertEqual(
            limits.resolve_seconds(DIAL, self.ws, 1), _free())
        self.assertEqual(
            limits.resolve_seconds(DIAL, self.ws, 10 ** 9), _ceiling())
        # Halfway up is above the free floor, so it only survives where the
        # range actually extends past it.
        midway = (_free() + _ceiling()) // 2
        self.assertEqual(
            limits.resolve_seconds(DIAL, self.ws, midway),
            midway if _ceiling() >= midway else _free())

    def test_nonsense_falls_back_rather_than_raising(self):
        self.assertEqual(
            limits.resolve_seconds(DIAL, self.ws, "later"), _free())

    def test_with_a_ledger_the_grant_is_the_cap(self):
        # The billed shape, which this host cannot reach for real: a paid grant
        # binds below the ceiling, so a workspace cannot use time nobody bought.
        granted = _free() * 2
        with mock.patch.object(limits, "_has_ledger", return_value=True), \
             mock.patch("toto.quota.times.effective_seconds", return_value=granted):
            low, high = limits.allowed_range(DIAL, self.ws)
            self.assertEqual((low, high), (_free(), granted))
            self.assertEqual(
                limits.resolve_seconds(DIAL, self.ws, 10 ** 9), granted)

    def test_a_tightened_bound_applies_without_a_re_save(self):
        """Clamped on the way OUT too: a grant that lapses takes effect at once.

        Read through `resolve_seconds` rather than through a lab's own
        accessor. It went through `workspace_settings.idle_seconds` until
        2026-09-10, which is how it came to be testing a dial that was about to
        be deleted; the rule being pinned is `limits.py`'s, and asking
        `limits.py` directly is what keeps it that way.
        """
        with mock.patch.object(limits, "_has_ledger", return_value=True), \
             mock.patch("toto.quota.times.effective_seconds",
                        return_value=_free()):
            self.assertEqual(
                limits.resolve_seconds(DIAL, self.ws, 10 ** 9), _free())


class EnvFieldTests(AmbrosiaTestCase):
    """The ENV kind's guarantees, tested against a field this test declares.

    MOVED HERE FROM the settings endpoint on 2026-09-10. They used to post
    `{"settings": {"env": …}}` at dracena, which declared an `env` field so a
    kernel could be handed extra variables. That field is deleted — a Run's
    environment is assembled by the manager from a fixed allowlist — and NO
    installed lab declares an ENV field today.

    The cleaning code is not deleted, and these tests are why it must not rot:
    the NUL-byte refusal in particular is a guard against a deployed-host-only
    500 (postgres jsonb cannot hold a NUL; the sqlite test database takes it
    happily). Declaring the field locally keeps that proven without pretending
    a lab offers one.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace(name="Enved", kind="python")
        self.fields = (settings_spec.Field(
            key="env", kind=settings_spec.ENV, label="Environment",
            default=dict),)

    def _clean(self, value):
        return settings_spec.clean(self.fields, {"env": value},
                                   workspace=self.ws)

    def test_a_bad_variable_name_is_refused(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError) as caught:
            self._clean({"NOT A NAME": "1"})
        self.assertIn("env", caught.exception.message_dict)

    def test_a_null_byte_in_a_value_is_refused(self):
        # postgres jsonb cannot store a NUL, so without this guard a deployed
        # host answers 500 from the database where the sqlite test database
        # accepts it silently — the worst kind of environment-only failure.
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError) as caught:
            self._clean({"TOKEN": "a\x00b"})
        self.assertIn("env", caught.exception.message_dict)

    def test_a_good_mapping_round_trips(self):
        self.assertEqual(self._clean({"API_HOST": "example.test"}),
                         {"env": {"API_HOST": "example.test"}})

    def test_no_installed_lab_offers_one(self):
        """Why the tests above declare their own field.

        If this goes red, a lab started accepting caller-supplied environment
        variables again — which for a capsule means the manager's allowlist is
        no longer the only source. Worth a look before deleting this.
        """
        from toto.ambrosia import registry

        for kind, app in registry._BY_KIND.items():
            with self.subTest(kind=kind):
                kinds = {f.kind for f in app.settings_fields()}
                self.assertNotIn(settings_spec.ENV, kinds,
                                 f"the {app.namespace} lab declares an ENV field")


class SettingsSpecTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace(name="Spec", kind="python")

    def test_effective_fills_in_every_declared_field(self):
        """Every DECLARED field, whatever a lab happens to declare.

        It named `idle_seconds` and `inline_plots` until 2026-09-10 and both
        are deleted, so the assertion is now written against the declaration
        itself. That is also the better test: the claim `effective` makes is
        "no declared key is missing", not "these three keys exist".
        """
        from toto.dracena import workspace_settings

        fields = workspace_settings.fields()
        values = settings_spec.effective(fields, {}, workspace=self.ws)
        self.assertEqual(set(values), {f.key for f in fields})
        self.assertIn("exec_timeout", values)

    def test_describe_carries_the_bounds_the_panel_renders(self):
        from toto.dracena import workspace_settings

        described = {f["key"]: f for f in settings_spec.describe(
            workspace_settings.fields(), workspace=self.ws)}
        self.assertEqual(described["exec_timeout"]["min"],
                         workspace_settings.EXEC_TIMEOUT_MIN)
        self.assertEqual(described["exec_timeout"]["max"],
                         workspace_settings.EXEC_TIMEOUT_MAX)
        self.assertTrue(described["exec_timeout"]["needsExecute"])


class SettingsRoomTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace(name="Roomy", kind="python")

    def _room(self, user):
        self.client.force_login(user)
        return self.client.get(
            reverse("dracena:workspace", kwargs={"slug": self.ws.slug}))

    def test_the_room_carries_the_settings_button_and_drawer(self):
        body = self._room(self.owner).content.decode()
        self.assertIn("openSettings()", body)
        self.assertIn("Workspace settings", body)

    def test_the_old_bottom_disclosure_is_gone(self):
        # It was a second surface called "settings" holding a dead time-dial
        # card; the drawer absorbed it. `danger` was its Alpine flag.
        body = self._room(self.owner).content.decode()
        self.assertNotIn("x-data=\"{ danger:", body)

    def test_the_bar_labels_collapse_on_a_narrow_screen(self):
        # The room has no media queries of its own — the collapse is Tailwind's
        # sm: breakpoint on the label spans, so assert the class is there.
        body = self._room(self.owner).content.decode()
        self.assertIn('class="hidden sm:inline">Run file', body)

    def test_a_readonly_viewer_gets_no_save_button(self):
        theirs = self.make_workspace(owner=self.other, name="Theirs", kind="python")
        self.client.force_login(self.admin)
        body = self.client.get(
            reverse("dracena:workspace", kwargs={"slug": theirs.slug})).content.decode()
        self.assertIn("openSettings()", body)          # they may look
        self.assertNotIn("saveSettings()", body)       # and not save
