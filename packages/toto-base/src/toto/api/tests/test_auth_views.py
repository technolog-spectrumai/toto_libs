"""Auth, identity and capability endpoints.

These tests moved here from ``toto.telegraph.tests.test_api_views`` when the views
themselves moved out of the chat app: none of them is chat. They exercise the canonical
``/api/`` prefix; the legacy ``/telegraph/api/`` alias has its own test at the bottom.
"""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

User = get_user_model()


class MeshGateApiViewTests(TestCase):
    """The decentralized data-mesh server gate: members read gated data from the server;
    non-members get 403 and must peer-pull."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="meshu", password="pass123")
        self.group, _ = Group.objects.get_or_create(name="data_mesh")

    def test_me_mesh_non_member(self):
        self.client.force_login(self.user)
        res = self.client.get("/api/me/mesh/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["member"])
        self.assertEqual(data["allowed"], [])
        self.assertIn("missions", data["gated"])

    def test_me_mesh_member(self):
        self.user.groups.add(self.group)
        self.client.force_login(self.user)
        data = self.client.get("/api/me/mesh/").json()
        self.assertTrue(data["member"])
        self.assertIn("missions", data["allowed"])

    # Missions are the gated (mesh) domain; the gate short-circuits in dispatch before
    # the view runs, so a missing project id still 403s for a non-member.
    GATED_URL = "/kanban/api/projects/1/missions/"

    def _needs_the_gated_api(self):
        # Kanban's missions API lives where the boards do (aurelian); zenobia
        # parked the boards and mounts no /kanban/ at all.
        from django.urls import Resolver404, resolve

        try:
            resolve(self.GATED_URL)
        except Resolver404:
            self.skipTest("this host mounts no gated missions API")

    def test_gated_read_denied_for_non_member(self):
        self._needs_the_gated_api()
        self.client.force_login(self.user)
        res = self.client.get(self.GATED_URL)
        self.assertEqual(res.status_code, 403)
        self.assertTrue(res.json().get("gated"))

    def test_gated_read_allowed_for_member(self):
        self._needs_the_gated_api()
        self.user.groups.add(self.group)
        self.client.force_login(self.user)
        res = self.client.get(self.GATED_URL)
        # Member passes the gate (the view itself may 404 for a missing project).
        self.assertNotEqual(res.status_code, 403)
        self.assertNotEqual(res.status_code, 401)

    def test_non_member_blocked_unauthenticated(self):
        self._needs_the_gated_api()
        res = self.client.get(self.GATED_URL)
        self.assertEqual(res.status_code, 401)



class HealthApiViewTests(TestCase):
    def test_health_reports_the_service(self):
        res = self.client.get("/api/health/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        # Was "telegraph" while this endpoint lived in the chat app; it is a
        # framework-level health check and never was chat-specific.
        self.assertEqual(data["service"], "toto")


class LoginLogoutApiViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass123")

    def test_login_returns_token(self):
        res = self.client.post(
            "/api/login/",
            json.dumps({"username": "testuser", "password": "pass123"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertIn("token", data)

    def test_login_wrong_password(self):
        res = self.client.post(
            "/api/login/",
            json.dumps({"username": "testuser", "password": "wrong"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)

    def test_logout(self):
        self.client.force_login(self.user)
        res = self.client.post("/api/logout/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])

    def test_logout_with_a_bearer_token_ends_that_token(self):
        # 2026-09-30: it used to flush the request's (absent) cookie session
        # and leave the token signed in and on the Sessions list.
        from django.test import Client

        from toto.audit.models import AuditRecord
        from toto.core.models import UserSession

        token = self.client.post(
            "/api/login/",
            json.dumps({"username": "testuser", "password": "pass123"}),
            content_type="application/json",
        ).json()["token"]
        browser = Client()
        browser.force_login(self.user)
        browser_key = browser.session.session_key
        self.assertTrue(UserSession.objects.filter(session_key=token).exists())
        desktop = Client()
        bearer = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        self.assertEqual(desktop.get("/api/me/", **bearer).status_code, 200)

        res = desktop.post("/api/logout/", **bearer)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(desktop.get("/api/me/", **bearer).status_code, 401)
        self.assertFalse(UserSession.objects.filter(session_key=token).exists())
        self.assertTrue(AuditRecord.objects.filter(action="AUTH.LOGOUT",
                                                   actor_user=self.user).exists())
        # The member's other sessions are not touched.
        self.assertTrue(UserSession.objects.filter(session_key=browser_key).exists())
        self.assertEqual(browser.get("/api/me/").status_code, 200)


class MeApiViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="meuser", password="pass", first_name="Me", last_name="User"
        )

    def test_me_unauthenticated(self):
        res = self.client.get("/api/me/")
        self.assertEqual(res.status_code, 401)

    def test_me_authenticated(self):
        self.client.force_login(self.user)
        res = self.client.get("/api/me/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["username"], "meuser")

    def test_me_includes_default_language(self):
        self.client.force_login(self.user)
        data = self.client.get("/api/me/").json()
        self.assertEqual(data["language"], "en")

    def test_me_reflects_profile_language(self):
        from toto.people.models import Person
        Person.objects.create(user=self.user, display_name="Me", preferred_language="pl")
        self.client.force_login(self.user)
        data = self.client.get("/api/me/").json()
        self.assertEqual(data["language"], "pl")

    def test_patch_language_unauthenticated(self):
        res = self.client.patch(
            "/api/me/", data=json.dumps({"language": "pl"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)

    def test_patch_language_rejects_unsupported(self):
        self.client.force_login(self.user)
        res = self.client.patch(
            "/api/me/", data=json.dumps({"language": "fr"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)

    def test_patch_language_without_profile_is_ok_but_not_stored(self):
        self.client.force_login(self.user)
        res = self.client.patch(
            "/api/me/", data=json.dumps({"language": "pl"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["language"], "pl")
        self.assertFalse(body["stored"])

    def test_patch_language_persists_to_profile(self):
        from toto.people.models import Person
        person = Person.objects.create(user=self.user, display_name="Me", preferred_language="en")
        self.client.force_login(self.user)
        res = self.client.patch(
            "/api/me/", data=json.dumps({"language": "pl"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["stored"])
        person.refresh_from_db()
        self.assertEqual(person.preferred_language, "pl")



class AppsDescriptorApiTests(TestCase):
    """The /telegraph/api/apps/ capability descriptor — lets Enigma+ show only the apps
    the connected server (portal vs faros) actually installs.

    Asked signed in, as the desktop asks it: a host may keep it behind its login
    gate (zenobia does), and what is under test is the answer, not the door."""

    def setUp(self):
        self.client.force_login(
            User.objects.create_user(username="descriptor", password="pw"))

    def test_reports_all_known_features_as_bools(self):
        res = self.client.get("/api/apps/")
        self.assertEqual(res.status_code, 200)
        apps = res.json()["apps"]
        for key in ["chat", "vault", "tasks", "locations", "people", "events", "graph",
                    "aster", "beacon"]:
            self.assertIn(key, apps)
            self.assertIsInstance(apps[key], bool)
        self.assertTrue(apps["chat"])  # telegraph is installed wherever this endpoint runs

    def test_uninstalled_app_reported_false(self):
        # Simulate a faros-style server without the knowledge graph (ravioli).
        from unittest.mock import patch
        from django.apps import apps as django_apps

        real = django_apps.is_installed

        def fake(label):
            return False if label == "toto.ravioli" else real(label)

        with patch("django.apps.apps.is_installed", side_effect=fake):
            apps = self.client.get("/api/apps/").json()["apps"]
        self.assertFalse(apps["graph"])
        self.assertTrue(apps["vault"])

    def test_host_carried_apps_reported_false_when_absent(self):
        # aster/beacon live in the faros host, not the library, so on portal (and
        # on any vault-only host) they are simply not installed. Enigma reads
        # these to hide sealed invites and location sharing rather than failing
        # after the press.
        from unittest.mock import patch
        from django.apps import apps as django_apps

        real = django_apps.is_installed

        def fake(label):
            return False if label in ("toto.aster", "toto.beacon") else real(label)

        with patch("django.apps.apps.is_installed", side_effect=fake):
            apps = self.client.get("/api/apps/").json()["apps"]
        self.assertFalse(apps["aster"])
        self.assertFalse(apps["beacon"])
        self.assertTrue(apps["vault"])


class LegacyTelegraphApiAliasTests(TestCase):
    """A shipped enigma desktop binary calls /telegraph/api/{login,logout,me}/.

    It cannot be updated in lockstep with the server, so that prefix must keep working
    even though the views live in toto.api and the chat app is being renamed.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="legacy", password="pass123")

    def test_legacy_login_still_works(self):
        res = self.client.post(
            "/telegraph/api/login/",
            data=json.dumps({"username": "legacy", "password": "pass123"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["token"])

    def test_legacy_me_still_works(self):
        self.client.force_login(self.user)
        res = self.client.get("/telegraph/api/me/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["username"], "legacy")

    def test_legacy_logout_still_works(self):
        self.client.force_login(self.user)
        res = self.client.post("/telegraph/api/logout/")
        self.assertEqual(res.status_code, 200)
