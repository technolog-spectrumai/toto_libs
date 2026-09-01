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





def _ceiling() -> int:
    """How far `dracena.kernel_idle` may be turned up ON THIS HOST.

    See toto.ambrosia.limits: with a ledger installed a paid grant binds the
    range, so an ungranted workspace cannot exceed the free default; without
    one the dial's declaration binds.
    """
    from django.apps import apps

    from toto.quota import times

    if apps.is_installed("toto.tax"):
        return times.free_seconds("dracena.kernel_idle")
    return times.ceiling_seconds("dracena.kernel_idle")


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
        self.client.force_login(self.owner)
        self._post({"settings": {"exec_timeout": 45, "inline_plots": False}})

        fresh = Workspace.objects.get(pk=self.ws.pk)
        stored = fresh.settings_for("dracena")
        self.assertEqual(stored["exec_timeout"], 45)
        self.assertIs(stored["inline_plots"], False)

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

    def test_a_bad_environment_variable_name_is_refused(self):
        self.client.force_login(self.owner)
        response = self._post({"settings": {"env": {"NOT A NAME": "1"}}})
        self.assertEqual(response.status_code, 409)
        self.assertIn("env", response.json()["fields"])

    def test_a_null_byte_in_a_value_is_refused(self):
        # postgres jsonb cannot store a NUL, so without this guard a deployed
        # host answers 500 from the database where the sqlite test database
        # accepts it silently — the worst kind of environment-only failure.
        self.client.force_login(self.owner)
        response = self._post({"settings": {"env": {"TOKEN": "a\x00b"}}})
        self.assertEqual(response.status_code, 409)
        self.assertIn("env", response.json()["fields"])

    def test_environment_variables_round_trip(self):
        self.client.force_login(self.owner)
        response = self._post({"settings": {"env": {"API_HOST": "example.test"}}})
        self.assertTrue(response.json()["ok"])
        self.ws.refresh_from_db()
        self.assertEqual(self.ws.settings_for("dracena")["env"],
                         {"API_HOST": "example.test"})

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

    def test_how_far_the_knob_turns_depends_on_whether_there_is_a_ledger(self):
        """"Cap always, charge where there is a ledger" — both halves.

        With no toto.tax the dial's DECLARED ceiling is the cap, because there
        is nothing to sell the range with. With one, an ungranted workspace
        sits at the free default and buying is what raises it. This test moved
        from a ledger-free host to one with a ledger, which is why it asserts
        the rule rather than a number.
        """
        low, high = limits.allowed_range("dracena.kernel_idle", self.ws)
        self.assertEqual(low, 3600)         # free — the floor, always
        self.assertEqual(high, _ceiling())

    def test_nothing_stored_means_the_entitlement_unchanged(self):
        # An untouched workspace behaves exactly as it did before the feature.
        self.assertEqual(
            limits.resolve_seconds("dracena.kernel_idle", self.ws, None), 3600)

    def test_a_stored_value_is_clamped_at_both_ends(self):
        self.assertEqual(
            limits.resolve_seconds("dracena.kernel_idle", self.ws, 10), 3600)
        self.assertEqual(
            limits.resolve_seconds("dracena.kernel_idle", self.ws, 10 ** 9),
            _ceiling())
        # 7200 is above the free floor, so it only survives where the range
        # actually extends past it.
        self.assertEqual(
            limits.resolve_seconds("dracena.kernel_idle", self.ws, 7200),
            7200 if _ceiling() >= 7200 else 3600)

    def test_nonsense_falls_back_rather_than_raising(self):
        self.assertEqual(
            limits.resolve_seconds("dracena.kernel_idle", self.ws, "later"), 3600)

    def test_with_a_ledger_the_grant_is_the_cap(self):
        # The billed shape, which this host cannot reach for real: a paid grant
        # binds below the ceiling, so a workspace cannot use time nobody bought.
        with mock.patch.object(limits, "_has_ledger", return_value=True), \
             mock.patch("toto.quota.times.effective_seconds", return_value=7200):
            low, high = limits.allowed_range("dracena.kernel_idle", self.ws)
            self.assertEqual((low, high), (3600, 7200))
            self.assertEqual(
                limits.resolve_seconds("dracena.kernel_idle", self.ws, 604800), 7200)

    def test_a_tightened_bound_applies_without_a_re_save(self):
        # Clamped on the way OUT too: a grant that lapses takes effect at once.
        self.ws.settings = {"dracena": {"idle_seconds": 604800}}
        with mock.patch.object(limits, "_has_ledger", return_value=True), \
             mock.patch("toto.quota.times.effective_seconds", return_value=3600):
            from toto.dracena import workspace_settings

            self.assertEqual(workspace_settings.idle_seconds(self.ws), 3600)


class SettingsSpecTests(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        self.ws = self.make_workspace(name="Spec", kind="python")

    def test_effective_fills_in_every_declared_field(self):
        from toto.dracena import workspace_settings

        values = settings_spec.effective(
            workspace_settings.fields(), {}, workspace=self.ws)
        self.assertIn("exec_timeout", values)
        self.assertIn("idle_seconds", values)
        self.assertIs(values["inline_plots"], True)

    def test_describe_carries_the_bounds_the_panel_renders(self):
        from toto.dracena import workspace_settings

        described = {f["key"]: f for f in settings_spec.describe(
            workspace_settings.fields(), workspace=self.ws)}
        self.assertEqual(described["idle_seconds"]["min"], 3600)
        self.assertEqual(described["idle_seconds"]["max"], _ceiling())
        self.assertTrue(described["env"]["needsExecute"])
        self.assertTrue(described["inline_plots"]["restartHint"])


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
