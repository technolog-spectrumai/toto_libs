"""Tests for toto.nomad — app-managed Tor onion identity.

Run (faros settings) from the repo root:
    DJANGO_SETTINGS_MODULE=faros.settings python faros/manage.py test toto.nomad

The Tor control protocol (``tor_control``) is mocked throughout — these tests
never touch a live tor.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from toto.nomad import keystore, service
from toto.nomad.middleware import NomadReachabilityMiddleware
from toto.nomad.models import NomadSettings, OnionIdentity
from toto.nomad.plugins.profile_plugins import OnionIdentityPlugin

User = get_user_model()

# Host-repo dependency: a few tests exercise portal/scripts/deploy.py. In the
# monorepo it sat at parents[3]; the split sibling layout (toto_libs next to
# the portal checkout) resolves to the same place. Standalone installs of the
# toto library skip those tests.
_DEPLOY_PY = Path(__file__).resolve().parents[3] / "portal" / "scripts" / "deploy.py"


class NomadTestBase(TestCase):
    def setUp(self):
        cache.clear()  # NomadSettings.load() is cached; isolate tests
        self.addCleanup(cache.clear)
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


@unittest.skipUnless(_DEPLOY_PY.exists(), "portal/scripts/deploy.py not present (standalone toto install)")
class TorHashPasswordTests(TestCase):
    def test_deploy_hash_password_format(self):
        # The pure-python S2K helper must emit the "16:" + 58 hex char form tor expects.
        import importlib.util

        spec = importlib.util.spec_from_file_location("toto_deploy", _DEPLOY_PY)
        deploy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(deploy)

        hashed = deploy.tor_hash_password("hunter2")
        self.assertTrue(hashed.startswith("16:"))
        body = hashed[3:]
        self.assertEqual(len(body), 58)  # 8B salt + 1B indicator + 20B sha1 = 29B -> 58 hex
        int(body, 16)  # all hex
        self.assertEqual(body[16:18], "60")  # the count indicator byte


class ReachabilityMiddlewareTests(NomadTestBase):
    def _run(self, transport=None):
        mw = NomadReachabilityMiddleware(lambda r: HttpResponse("ok"))
        req = RequestFactory().get("/")
        if transport:
            req.META["HTTP_X_FAROS_TRANSPORT"] = transport
        return mw(req)

    def test_no_header_allowed(self):
        self.assertEqual(self._run().status_code, 200)

    def test_clearnet_allowed_when_enabled(self):
        self.assertEqual(self._run("clearnet").status_code, 200)

    def test_clearnet_blocked_when_disabled(self):
        s = NomadSettings.load()
        s.clearnet_enabled = False
        s.save()
        self.assertEqual(self._run("clearnet").status_code, 404)

    def test_onion_blocked_when_disabled_but_clearnet_served(self):
        s = NomadSettings.load()
        s.onion_enabled = False
        s.save()
        self.assertEqual(self._run("onion").status_code, 404)
        self.assertEqual(self._run("clearnet").status_code, 200)


class ReachabilityServiceTests(NomadTestBase):
    @mock.patch("toto.nomad.tor_control.unpublish")
    def test_disable_onion_unpublishes_and_deactivates(self, unpublish):
        keystore.save_key("svc", "ED25519-V3:K")
        OnionIdentity.objects.create(service_id="svc", is_active=True)

        service.set_onion_enabled(False)

        unpublish.assert_called_once_with("svc")
        self.assertFalse(OnionIdentity.objects.get(service_id="svc").is_active)
        self.assertFalse(NomadSettings.load().onion_enabled)

    @mock.patch("toto.nomad.tor_control.publish", return_value="svc")
    def test_enable_onion_republishes(self, publish):
        s = NomadSettings.load()
        s.onion_enabled = False
        s.save()
        keystore.save_key("svc", "ED25519-V3:K")

        service.set_onion_enabled(True)

        publish.assert_called_once_with("ED25519-V3:K")
        self.assertTrue(NomadSettings.load().onion_enabled)

    def test_lockout_guard_blocks_disabling_last_transport(self):
        s = NomadSettings.load()
        s.onion_enabled = False  # only clearnet remains on
        s.save()
        with self.assertRaises(ValueError):
            service.set_clearnet_enabled(False)

    def test_ensure_onion_noop_when_disabled(self):
        s = NomadSettings.load()
        s.onion_enabled = False
        s.save()
        self.assertIsNone(service.ensure_onion())


class SetReachabilityViewTests(NomadTestBase):
    @mock.patch("toto.nomad.service.set_clearnet_enabled")
    def test_superuser_can_toggle(self, setter):
        admin = User.objects.create_superuser("a4", "a4@x.com", "pw")
        c = Client()
        c.force_login(admin)

        resp = c.post(reverse("nomad:set_reachability"), {"transport": "clearnet", "enabled": "0"})

        self.assertEqual(resp.status_code, 302)
        setter.assert_called_once()
        self.assertFalse(setter.call_args.args[0])  # enabled=False

    @mock.patch("toto.nomad.service.set_clearnet_enabled")
    def test_non_superuser_forbidden(self, setter):
        bob = User.objects.create_user("bob3", "b3@x.com", "pw")
        c = Client()
        c.force_login(bob)

        resp = c.post(reverse("nomad:set_reachability"), {"transport": "clearnet", "enabled": "0"})

        self.assertEqual(resp.status_code, 403)
        setter.assert_not_called()


@unittest.skipUnless(_DEPLOY_PY.exists(), "portal/scripts/deploy.py not present (standalone toto install)")
class NginxTransportHeaderTests(TestCase):
    def _deploy(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("toto_deploy_nginx", _DEPLOY_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_two_listeners_with_transport_headers(self):
        conf = self._deploy().build_nginx_conf({
            "deployment": {"name": "faros_test", "project_dir": "faros"},
            "services": {"websockets": True},
            "ssl": {"mode": "gervazy"},
            "env": {},
            "reachability": {"transport_header": True, "onion_listener_port": 8443},
        })
        self.assertIn("listen 443 ssl", conf)
        self.assertIn("listen 8443 ssl", conf)
        self.assertIn("X-Faros-Transport clearnet", conf)
        self.assertIn("X-Faros-Transport onion", conf)

    def test_single_listener_when_flag_off(self):
        conf = self._deploy().build_nginx_conf({
            "deployment": {"name": "faros_test", "project_dir": "faros"},
            "services": {},
            "ssl": {"mode": "gervazy"},
            "env": {},
        })
        self.assertIn("listen 443 ssl", conf)
        self.assertNotIn("listen 8443 ssl", conf)
        self.assertNotIn("X-Faros-Transport", conf)
