"""What every container start runs (2026-09-29): `init_platform` →
`migrate`, then `init_data` → `create_user`, `bootstrap_plans`, the fonts,
`create_theme`, `create_platform` and the federation. deploy/entrypoint.sh
calls it on EVERY start, so a second run must change nothing but what it is
told to (the admin's password), and a missing seed file must fail in words.

Hermetic: the seed data (fonts.json, themes/, img/) is a scratch TOTO_DATA_DIR
written per test, and media goes to a scratch MEDIA_ROOT."""

import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from toto.core.models import ColorMix, Federation, Font, Platform, Theme
from toto.people.models import Person

User = get_user_model()

# The smallest valid PNG: a 1x1 white pixel.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63f8ffff3f0005fe02fea7d6a4c80000000049454e44ae426082")

SEED_ENV = {"ADMIN_USERNAME": "admin", "ADMIN_EMAIL": "", "PLATFORM_NAME": "Zenobia Test",
            "PLATFORM_DOMAIN": "zen.example.org", "PLATFORM_AUTHOR": "Ops",
            "ADMIN_DISPLAY_NAME": "", "ADMIN_FIRST_NAME": "", "ADMIN_LAST_NAME": "",
            "ADMIN_PERSON_EMAIL": "", "ADMIN_PERSON_PHONE": ""}


class SeedCase(TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="seed-data-"))
        self.media = tempfile.mkdtemp(prefix="seed-media-")
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        (self.data / "themes").mkdir()
        (self.data / "img").mkdir()
        (self.data / "fonts.json").write_text(json.dumps(
            {"Orbitron": "https://fonts.example/orbitron.css"}), encoding="utf-8")
        (self.data / "themes" / "amazing.json").write_text(json.dumps({
            "name": "Amazing Moon", "font": "Orbitron",
            "colors": {"accent-1": "#4f5fa1", "accent-2": "#2f3d63", "not-a-field": "x"},
            "header": {"light": "bg-white"}}), encoding="utf-8")
        (self.data / "img" / "okti.png").write_bytes(PNG)
        overrides = override_settings(TOTO_DATA_DIR=str(self.data), MEDIA_ROOT=self.media,
                                      PLATFORM_LOGO_PATH="no/such/logo.png")
        overrides.enable()
        self.addCleanup(overrides.disable)
        env = mock.patch.dict(os.environ, SEED_ENV)
        env.start()
        self.addCleanup(env.stop)

    def seed(self, password="first-pw", **env):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env):
            call_command("init_data", password=password, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()


class FirstStartTests(SeedCase):
    def test_a_first_start_seeds_the_admin_the_theme_and_an_active_platform(self):
        self.seed()
        admin = User.objects.get(username="admin")
        self.assertTrue(admin.is_superuser and admin.is_staff)
        self.assertTrue(admin.check_password("first-pw"))
        platform = Platform.objects.get(active=True)
        self.assertEqual((platform.site_name, platform.domain, platform.author),
                         ("Zenobia Test", "zen.example.org", "Ops"))
        self.assertEqual(platform.theme.name, "Amazing Moon")
        self.assertEqual(platform.theme.font.name, "Orbitron")
        self.assertEqual(platform.api_owner, admin)
        self.assertEqual(platform.federation.name, "Toto-Federation")

    def test_the_theme_takes_only_the_colours_it_has_fields_for(self):
        self.seed()
        mix = ColorMix.objects.get(name="Amazing Moon")
        self.assertEqual((mix.accent_1, mix.accent_2), ("#4f5fa1", "#2f3d63"))
        self.assertEqual(Theme.objects.get(name="Amazing Moon").header, {"light": "bg-white"})

    def test_without_a_configured_logo_the_platform_wears_the_mascot(self):
        self.seed()
        platform = Platform.objects.get(active=True)
        self.assertTrue(os.path.basename(platform.logo.name).startswith("okti"))
        self.assertTrue(Federation.objects.get().logo)

    def test_the_admin_without_an_address_gets_one_on_the_platform_s_domain(self):
        self.seed()
        self.assertEqual(User.objects.get(username="admin").email, "admin@zen.example.org")

    def test_a_given_admin_address_and_names_are_used(self):
        self.seed(ADMIN_EMAIL="ops@example.org", ADMIN_FIRST_NAME="Ada", ADMIN_LAST_NAME="L")
        admin = User.objects.get(username="admin")
        self.assertEqual((admin.email, admin.first_name, admin.last_name),
                         ("ops@example.org", "Ada", "L"))

    def test_a_display_name_gives_the_admin_a_person_with_contact_details(self):
        self.seed(ADMIN_DISPLAY_NAME="Operations", ADMIN_EMAIL="ops@example.org",
                  ADMIN_PERSON_PHONE="+48 600 000 000")
        person = Person.objects.get(user__username="admin")
        self.assertEqual((person.display_name, person.email, person.phone),
                         ("Operations", "ops@example.org", "+48 600 000 000"))

    @override_settings(FEDERATION_ENABLED=False)
    def test_a_host_without_federation_is_left_unfederated(self):
        self.seed()
        self.assertIsNone(Platform.objects.get(active=True).federation)
        self.assertFalse(Federation.objects.exists())

    def test_the_admin_holds_the_superuser_plan(self):
        if not apps.is_installed("toto.subscriptions"):
            self.skipTest("no plans on this host")
        from toto.subscriptions.models import superuser_plan_active

        self.seed()
        self.assertTrue(superuser_plan_active(User.objects.get(username="admin")))


class RestartTests(SeedCase):
    def test_a_second_start_changes_nothing_but_the_password(self):
        self.seed(password="first-pw")
        self.seed(password="second-pw")
        self.assertEqual(User.objects.filter(username="admin").count(), 1)
        self.assertTrue(User.objects.get(username="admin").check_password("second-pw"))
        self.assertEqual(Platform.objects.count(), 1)
        self.assertEqual((Theme.objects.count(), Font.objects.count(),
                          Federation.objects.count()), (1, 1, 1))

    def test_a_password_changed_on_the_profile_survives_a_restart(self):
        # Stage 51: every start set ADMIN_PASSWORD again, so a rotation made
        # on the profile after a leak was undone by the next restart.
        self.seed(password="env-pw")
        admin = User.objects.get(username="admin")
        admin.set_password("rotated-pw")
        admin.save()
        self.seed(password="env-pw")
        admin = User.objects.get(username="admin")
        self.assertTrue(admin.check_password("rotated-pw"))
        self.assertFalse(admin.check_password("env-pw"))
        self.assertTrue(admin.is_superuser and admin.is_staff)

    def test_an_admin_from_before_the_marker_takes_the_password_once(self):
        # A server whose admin predates the marker (admin/admin) converges on
        # the configured password at its next start, as deploy.py expects.
        User.objects.create_superuser("admin", "", "admin")
        self.seed(password="configured-pw")
        self.assertTrue(User.objects.get(username="admin").check_password("configured-pw"))

    def test_the_marker_is_not_the_password(self):
        from toto.core.models import BootstrapMarker

        self.seed(password="env-pw")
        self.assertNotIn("env-pw", " ".join(BootstrapMarker.objects.values_list("value", flat=True)))

    def test_a_restart_keeps_the_logo_it_already_assigned(self):
        self.seed()
        first = Platform.objects.get().logo.name
        self.seed()
        self.assertEqual(Platform.objects.get().logo.name, first)

    def test_a_restart_restores_an_admin_who_lost_the_flags(self):
        self.seed()
        User.objects.filter(username="admin").update(is_superuser=False, is_staff=False)
        self.seed()
        admin = User.objects.get(username="admin")
        self.assertTrue(admin.is_superuser and admin.is_staff)


class MissingSeedTests(SeedCase):
    def test_an_unreadable_theme_stops_before_any_platform_and_says_why(self):
        (self.data / "themes" / "amazing.json").write_text("{broken", encoding="utf-8")
        _, err = self.seed()
        self.assertIn("Invalid JSON in theme file", err)
        self.assertIn("Theme not found. Initialization aborted.", err)
        self.assertFalse(Platform.objects.exists())
        self.assertTrue(User.objects.filter(username="admin").exists())

    def test_a_theme_file_missing_its_keys_is_refused(self):
        (self.data / "themes" / "amazing.json").write_text(json.dumps({"name": "X"}))
        _, err = self.seed()
        self.assertIn("Missing required keys", err)
        self.assertFalse(Platform.objects.exists())

    def test_an_unreadable_fonts_file_is_reported(self):
        (self.data / "fonts.json").write_text("{nope", encoding="utf-8")
        Font.objects.create(name="Orbitron", cdn_link="https://fonts.example/o.css")
        _, err = self.seed()
        self.assertIn("Invalid JSON in fonts file", err)
        self.assertTrue(Platform.objects.filter(active=True).exists())

    @unittest.skip("SUSPECTED BUG toto/core/management/commands/create_theme.py:17-19 - an "
                   "unknown font raises RuntimeError(None) (the message line after it is "
                   "dead code), so a fonts.json missing the theme's font crashes init_data "
                   "- and the container start - with 'RuntimeError: None'.")
    def test_a_theme_whose_font_is_missing_fails_in_words(self):
        (self.data / "fonts.json").unlink()
        _, err = self.seed()
        self.assertIn("Fonts file not found", err)
        self.assertIn("Orbitron", err)


class CreatePlatformTests(SeedCase):
    def setUp(self):
        super().setUp()
        User.objects.create_superuser("admin", "a@example.org", "pw")

    def run_it(self, *args):
        out, err = io.StringIO(), io.StringIO()
        call_command("create_platform", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_without_the_admin_account_there_is_no_platform(self):
        with mock.patch.dict(os.environ, {"ADMIN_USERNAME": "nobody"}), \
                self.assertRaisesMessage(CommandError, "Admin user 'nobody' not found."):
            self.run_it("Site", "zen.example.org", "Ops")
        self.assertFalse(Platform.objects.exists())

    def test_a_missing_theme_is_a_warning_not_a_failure(self):
        _, err = self.run_it("Site", "zen.example.org", "Ops", "--theme_id=9999")
        self.assertIn("Theme 9999 not found.", err)
        self.assertIsNone(Platform.objects.get().theme)

    def test_the_same_domain_is_updated_in_place(self):
        self.run_it("Old", "zen.example.org", "Ops")
        out, _ = self.run_it("New", "zen.example.org", "Ops")
        self.assertIn("Updated Platform: New", out)
        self.assertEqual(list(Platform.objects.values_list("site_name", flat=True)), ["New"])

    def test_a_configured_logo_replaces_the_mascot(self):
        self.run_it("Site", "zen.example.org", "Ops")
        crown = self.data / "img" / "crown.png"
        crown.write_bytes(PNG)
        with override_settings(PLATFORM_LOGO_PATH=str(crown)):
            self.run_it("Site", "zen.example.org", "Ops")
        self.assertTrue(os.path.basename(Platform.objects.get().logo.name).startswith("crown"))

    def test_no_logo_anywhere_is_a_warning_and_the_platform_still_lands(self):
        (self.data / "img" / "okti.png").unlink()
        _, err = self.run_it("Site", "zen.example.org", "Ops")
        self.assertIn("Platform logo not found", err)
        self.assertFalse(Platform.objects.get().logo)


class CreateUserCommandTests(TestCase):
    def run_it(self, *args, **kwargs):
        out = io.StringIO()
        call_command("create_user", *args, stdout=out, **kwargs)
        return out.getvalue()

    def test_keep_password_sets_it_only_on_a_new_account(self):
        self.run_it("ops", "pw1", keep_password=True)
        self.run_it("ops", "pw2", keep_password=True)
        self.assertTrue(User.objects.get(username="ops").check_password("pw1"))

    def test_an_account_is_created_then_updated_never_duplicated(self):
        self.assertIn("created superuser: ops", self.run_it("ops", "pw1"))
        self.assertIn("updated superuser: ops", self.run_it("ops", "pw2"))
        self.assertEqual(User.objects.filter(username="ops").count(), 1)
        self.assertTrue(User.objects.get(username="ops").check_password("pw2"))

    def test_without_the_admin_flag_the_account_is_plain(self):
        self.run_it("ops", "pw")
        user = User.objects.get(username="ops")
        self.assertFalse(user.is_superuser or user.is_staff)

    def test_the_admin_flag_makes_a_superuser_with_staff(self):
        self.run_it("ops", "pw", admin=True)
        user = User.objects.get(username="ops")
        self.assertTrue(user.is_superuser and user.is_staff)

    def test_an_existing_address_is_kept_when_none_is_given(self):
        User.objects.create_user("ops", "real@example.org", "pw")
        self.run_it("ops", "pw2")
        self.assertEqual(User.objects.get(username="ops").email, "real@example.org")

    def test_a_loopback_domain_gives_the_reserved_invalid_one(self):
        with mock.patch.dict(os.environ, {"PLATFORM_DOMAIN": "http://localhost/"}):
            self.run_it("ops", "pw")
        self.assertEqual(User.objects.get(username="ops").email, "ops@localhost.invalid")

    @unittest.skip("SUSPECTED BUG toto/core/management/commands/create_user.py:46-50 (and "
                   "bootstrap_users._default_email:82-86) - the scheme and path are "
                   "stripped from PLATFORM_DOMAIN but a port is not: 'localhost:8000' "
                   "gives 'ops@localhost:8000', which is not an email address.")
    def test_a_domain_with_a_port_still_gives_a_valid_address(self):
        from django.core.validators import validate_email

        with mock.patch.dict(os.environ, {"PLATFORM_DOMAIN": "https://zen.example.org:8443/"}):
            self.run_it("ops", "pw")
        validate_email(User.objects.get(username="ops").email)


class InitPlatformTests(TestCase):
    def test_it_migrates_first_then_seeds_with_the_given_password(self):
        target = "toto.core.management.commands.init_platform.call_command"
        with mock.patch(target) as called:
            call_command("init_platform", password="s3cret", stdout=io.StringIO())
        self.assertEqual([c.args[0] for c in called.call_args_list], ["migrate", "init_data"])
        self.assertEqual(called.call_args_list[1].kwargs, {"password": "s3cret"})

    def test_the_environment_gives_the_password_off_the_command_line(self):
        """Stage 51: entrypoint.sh no longer passes --password (the host's
        `ps` showed it); the command reads ADMIN_PASSWORD itself."""
        target = "toto.core.management.commands.init_platform.call_command"
        with mock.patch.dict(os.environ, {"ADMIN_PASSWORD": "from-the-env"}), \
                mock.patch(target) as called:
            call_command("init_platform", stdout=io.StringIO())
        self.assertEqual(called.call_args_list[1].kwargs, {"password": "from-the-env"})

    def test_no_password_anywhere_is_refused_before_anything_runs(self):
        """It used to become the literal `admin`, a known superuser login."""
        from django.core.management.base import CommandError

        target = "toto.core.management.commands.init_platform.call_command"
        environ = {k: v for k, v in os.environ.items() if k != "ADMIN_PASSWORD"}
        for env in (environ, {**environ, "ADMIN_PASSWORD": ""}):
            with self.subTest(set="ADMIN_PASSWORD" in env), \
                    mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch(target) as called, self.assertRaises(CommandError) as caught:
                call_command("init_platform", "--reset", stdout=io.StringIO())
            self.assertIn("ADMIN_PASSWORD", str(caught.exception))
            self.assertEqual(called.call_args_list, [])


class CreateThemeTests(TestCase):
    def test_colours_that_are_not_json_create_no_theme(self):
        Font.objects.create(name="Orbitron", cdn_link="https://fonts.example/o.css")
        out = io.StringIO()
        call_command("create_theme", "--name", "T", "--font", "Orbitron", "--colors", "{x",
                     stdout=out)
        self.assertIn("Invalid JSON for --colors", out.getvalue())
        self.assertFalse(Theme.objects.exists())

    def test_a_second_theme_of_the_same_name_is_not_made(self):
        Font.objects.create(name="Orbitron", cdn_link="https://fonts.example/o.css")
        for _ in range(2):
            call_command("create_theme", "--name", "T", "--font", "Orbitron",
                         "--colors", '{"accent-1": "#000000"}', stdout=io.StringIO())
        self.assertEqual(Theme.objects.filter(name="T").count(), 1)


class IngressAllTests(TestCase):
    """`ingress_all --strict` is what the entrypoint runs for the compulsory set."""

    def run_it(self, **options):
        out = io.StringIO()
        try:
            call_command("ingress_all", stdout=out, **options)
            error = None
        except CommandError as exc:
            error = str(exc)
        return out.getvalue(), error

    @override_settings(INGRESS_MODE="realistic", INGRESS_ALLOWED_APPS=["toto.people"])
    def test_an_app_with_nothing_to_seed_is_not_a_failure_even_when_strict(self):
        out, error = self.run_it(strict=True)
        self.assertIsNone(error)
        self.assertIn("No ingress command found for 'people'", out)
        self.assertIn("Not Found: 1", out)

    @override_settings(INGRESS_MODE="realistic", INGRESS_ALLOWED_APPS=["toto.verbena"])
    def test_a_command_that_runs_is_counted_a_success(self):
        out, error = self.run_it(strict=True)
        self.assertIsNone(error)
        self.assertIn("Success: toto.verbena", out)
        self.assertIn("Failed: 0", out)

    @override_settings(INGRESS_MODE="realistic",
                       INGRESS_ALLOWED_APPS=["toto.verbena", "toto.people"])
    def test_a_command_that_raises_fails_a_strict_run_and_names_the_mode(self):
        target = "toto.verbena.management.commands.ingress_verbena.Command.process"
        with mock.patch(target, side_effect=RuntimeError("seed broke")):
            out, error = self.run_it(strict=True)
        self.assertEqual(error, "1 ingress command(s) failed in mode realistic")
        self.assertIn("Error running ingress for 'verbena': seed broke", out)
        self.assertIn("Not Found: 1", out)

    @override_settings(INGRESS_MODE="realistic", INGRESS_ALLOWED_APPS=["toto.verbena"])
    def test_the_mode_given_wins_over_the_host_setting_and_reaches_the_app(self):
        target = "toto.core.management.commands.ingress_all.call_command"
        with mock.patch(target) as called:
            out, _ = self.run_it(mode="full")
        self.assertIn("Ingress mode: full", out)
        self.assertEqual(called.call_args.kwargs["mode"], "full")
        self.assertEqual(called.call_args.args, ("ingress_verbena",))

    def test_an_app_is_named_by_its_last_dotted_part(self):
        from toto.core.management.commands.ingress_all import Command

        self.assertEqual(Command.resolve_app_label("toto.sso_core"), "sso_core")
