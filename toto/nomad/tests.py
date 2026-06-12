"""Tests for toto.nomad — app-managed Tor onion identity.

Run (faros settings) from the repo root:
    DJANGO_SETTINGS_MODULE=faros.settings python faros/manage.py test toto.nomad

The Tor control protocol (``tor_control``) is mocked throughout — these tests
never touch a live tor.
"""
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from toto.nomad import keystore, service
from toto.nomad.models import OnionIdentity
from toto.nomad.plugins.profile_plugins import OnionIdentityPlugin

User = get_user_model()


class NomadTestBase(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        ovr = override_settings(NOMAD_KEY_DIR=self.tmp)
        ovr.enable()
        self.addCleanup(ovr.disable)


class ServiceTests(NomadTestBase):
    @mock.patch("toto.nomad.tor_control.mint", return_value=("aaaaservice", "ED25519-V3:KEY1"))
    def test_ensure_mints_and_persists_when_empty(self, mint):
        onion = service.ensure_onion()

        self.assertEqual(onion, "aaaaservice")
        mint.assert_called_once()
        self.assertTrue(
            OnionIdentity.objects.filter(service_id="aaaaservice", is_active=True).exists()
        )
        self.assertEqual(keystore.load_key(), ("aaaaservice", "ED25519-V3:KEY1"))

    @mock.patch("toto.nomad.tor_control.publish", return_value="bbbbservice")
    @mock.patch("toto.nomad.tor_control.mint")
    def test_ensure_republishes_when_key_exists(self, mint, publish):
        keystore.save_key("bbbbservice", "ED25519-V3:KEY2")

        onion = service.ensure_onion()

        self.assertEqual(onion, "bbbbservice")
        mint.assert_not_called()
        publish.assert_called_once_with("ED25519-V3:KEY2")

    @mock.patch("toto.nomad.tor_control.unpublish")
    @mock.patch("toto.nomad.tor_control.mint", return_value=("newservice", "ED25519-V3:NEW"))
    def test_migrate_swaps_key_and_retires_old(self, mint, unpublish):
        keystore.save_key("oldservice", "ED25519-V3:OLD")
        OnionIdentity.objects.create(service_id="oldservice", is_active=True)

        onion = service.migrate_onion()

        self.assertEqual(onion, "newservice")
        unpublish.assert_called_once_with("oldservice")
        self.assertTrue(OnionIdentity.objects.get(service_id="newservice").is_active)
        self.assertFalse(OnionIdentity.objects.get(service_id="oldservice").is_active)
        self.assertEqual(keystore.load_key(), ("newservice", "ED25519-V3:NEW"))

    @mock.patch("toto.nomad.tor_control.unpublish")
    @mock.patch("toto.nomad.tor_control.mint", return_value=("svc2", "ED25519-V3:K"))
    def test_migrate_records_triggering_superuser(self, mint, unpublish):
        admin = User.objects.create_superuser("root", "root@x.com", "pw")
        service.migrate_onion(triggered_by=admin)
        self.assertEqual(OnionIdentity.objects.get(service_id="svc2").migrated_by, admin)

    def test_current_onion_none_when_unpublished(self):
        self.assertIsNone(service.current_onion())


class MigrateViewTests(NomadTestBase):
    @mock.patch("toto.nomad.service.migrate_onion", return_value="zzzservice")
    def test_superuser_can_migrate(self, migrate):
        admin = User.objects.create_superuser("admin", "a@x.com", "pw")
        c = Client()
        c.force_login(admin)

        resp = c.post(reverse("nomad:migrate"))

        self.assertEqual(resp.status_code, 302)
        migrate.assert_called_once()

    @mock.patch("toto.nomad.service.migrate_onion")
    def test_non_superuser_forbidden(self, migrate):
        bob = User.objects.create_user("bob", "b@x.com", "pw")
        c = Client()
        c.force_login(bob)

        resp = c.post(reverse("nomad:migrate"))

        self.assertEqual(resp.status_code, 403)
        migrate.assert_not_called()

    def test_anonymous_forbidden(self):
        resp = Client().post(reverse("nomad:migrate"))
        self.assertEqual(resp.status_code, 403)

    @mock.patch("toto.nomad.service.migrate_onion")
    def test_get_not_allowed(self, migrate):
        admin = User.objects.create_superuser("admin2", "a2@x.com", "pw")
        c = Client()
        c.force_login(admin)
        # GET isn't defined → 405; the migration must not run.
        resp = c.get(reverse("nomad:migrate"))
        self.assertEqual(resp.status_code, 405)
        migrate.assert_not_called()


class PluginVisibilityTests(NomadTestBase):
    def _request_for(self, user):
        req = RequestFactory().get("/")
        req.user = user
        return req

    def test_invisible_to_non_superuser(self):
        bob = User.objects.create_user("bob2", "b2@x.com", "pw")
        plugin = OnionIdentityPlugin()
        self.assertFalse(plugin.is_visible_for_profile(request=self._request_for(bob)))

    def test_visible_to_superuser(self):
        admin = User.objects.create_superuser("admin3", "a3@x.com", "pw")
        plugin = OnionIdentityPlugin()
        self.assertTrue(plugin.is_visible_for_profile(request=self._request_for(admin)))


class TorHashPasswordTests(TestCase):
    def test_deploy_hash_password_format(self):
        # The pure-python S2K helper must emit the "16:" + 58 hex char form tor expects.
        import importlib.util
        from pathlib import Path

        deploy_path = Path(__file__).resolve().parents[3] / "portal" / "scripts" / "deploy.py"
        spec = importlib.util.spec_from_file_location("toto_deploy", deploy_path)
        deploy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(deploy)

        hashed = deploy.tor_hash_password("hunter2")
        self.assertTrue(hashed.startswith("16:"))
        body = hashed[3:]
        self.assertEqual(len(body), 58)  # 8B salt + 1B indicator + 20B sha1 = 29B -> 58 hex
        int(body, 16)  # all hex
        self.assertEqual(body[16:18], "60")  # the count indicator byte
