import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

User = get_user_model()

# Deterministic password policy for these tests, independent of the host project's
# settings — the register endpoint must honour AUTH_PASSWORD_VALIDATORS.
_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


@override_settings(SSO_OPEN_REGISTRATION=True, AUTH_PASSWORD_VALIDATORS=_VALIDATORS)
class RegisterApiViewTests(TestCase):
    """Self-service signup for personal servers (Enigma Cloud), living in SSO."""

    URL = "/sso/api/register/"

    def _register(self, payload):
        return self.client.post(
            self.URL, json.dumps(payload), content_type="application/json"
        )

    def test_register_returns_token_that_works_as_bearer(self):
        res = self._register({"username": "newbie", "password": "longenough"})
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertIn("token", data)
        self.assertTrue(User.objects.filter(username="newbie").exists())
        # The token must authenticate a cookie-less client, like the login token.
        fresh = Client()
        me = fresh.get("/api/me/", HTTP_AUTHORIZATION=f"Bearer {data['token']}")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], "newbie")

    @override_settings(SSO_OPEN_REGISTRATION=False)
    def test_register_disabled(self):
        res = self._register({"username": "nope", "password": "longenough"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["error"], "Registration is disabled.")
        self.assertFalse(User.objects.filter(username="nope").exists())

    def test_register_duplicate_username(self):
        User.objects.create_user(username="taken", password="longenough")
        res = self._register({"username": "taken", "password": "longenough"})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], "Username already taken.")

    def test_register_duplicate_username_case_insensitive(self):
        User.objects.create_user(username="Taken", password="longenough")
        res = self._register({"username": "tAkEn", "password": "longenough"})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(User.objects.filter(username__iexact="taken").count(), 1)

    def test_register_short_password(self):
        res = self._register({"username": "shorty", "password": "seven77"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("8 characters", res.json()["error"])
        self.assertFalse(User.objects.filter(username="shorty").exists())

    def test_register_rejects_common_password(self):
        res = self._register({"username": "commonguy", "password": "password"})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(User.objects.filter(username="commonguy").exists())

    def test_register_rejects_all_numeric_password(self):
        res = self._register({"username": "numguy", "password": "48571029"})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(User.objects.filter(username="numguy").exists())

    def test_register_rejects_invalid_username(self):
        # Spaces / control chars fail the model's UnicodeUsernameValidator.
        for bad in ("has space", "tab\tuser", "sl/ash"):
            res = self._register({"username": bad, "password": "longenough"})
            self.assertEqual(res.status_code, 400, bad)
            self.assertEqual(res.json()["error"], "Invalid username.")
        self.assertEqual(User.objects.count(), 0)

    def test_register_rejects_overlong_username(self):
        res = self._register({"username": "u" * 200, "password": "longenough"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(User.objects.count(), 0)

    def test_register_missing_fields(self):
        for payload in ({}, {"username": "only"}, {"password": "longenough"},
                        {"username": "  ", "password": "longenough"}):
            res = self._register(payload)
            self.assertEqual(res.status_code, 400)
            self.assertEqual(res.json()["error"], "Username and password required.")

    def test_register_non_string_password_does_not_crash(self):
        res = self._register({"username": "typ", "password": 12345678})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"], "Username and password required.")

    def test_register_non_object_body(self):
        res = self.client.post(self.URL, json.dumps([1, 2, 3]), content_type="application/json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"], "Invalid JSON.")

    def test_register_invalid_json(self):
        res = self.client.post(self.URL, "not json", content_type="application/json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"], "Invalid JSON.")
