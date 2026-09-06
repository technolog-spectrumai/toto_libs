"""The store's login form, answered by the portal — every branch.

lakeFS Community has one user and no SSO; its remote authenticator POSTs the
login form's username+password to us and admits whoever we name, if that
name exists. So this is the whole access model for the store's UI, and every
outcome is asserted: the right superuser gets in, everybody else gets the
exact refusal shape lakeFS expects, and the door only opens from the compose
network.
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

BODY = {"username": "root", "password": "right"}


@override_settings(LAKEFS_ENABLED=True, LAKEFS_ADMIN_USERNAME="root",
                   PLATFORM_DOMAIN="portal.example.com")
class LakefsAuthenticatorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="T", author="A",
                                publication_year=2026, active=True)
        User = get_user_model()
        cls.root = User.objects.create_superuser(
            username="root", email="r@e.com", password="right")
        cls.staff = User.objects.create_user(
            username="clerk", email="c@e.com", password="right", is_staff=True)
        cls.plain = User.objects.create_user(
            username="plain", email="p@e.com", password="right")

    def post(self, body, **extra):
        return self.client.post(reverse("sso:lakefs_auth"), data=json.dumps(body),
                                content_type="application/json", **extra)

    def test_a_superuser_with_the_right_password_is_mapped_onto_the_store_user(self):
        response = self.post(BODY)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"external_user_identifier": "root"})

    def test_the_identifier_is_the_configured_name_not_the_login(self):
        """Any superuser maps onto the ONE lakeFS user, whatever they are
        called here — that user is the only one that exists."""
        get_user_model().objects.create_superuser(
            username="second", email="s@e.com", password="pw")
        response = self.post({"username": "second", "password": "pw"})
        self.assertEqual(response.json()["external_user_identifier"], "root")

    def test_a_wrong_password_is_refused_in_the_shape_lakefs_expects(self):
        response = self.post({"username": "root", "password": "wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"external_user_identifier": ""})

    def test_staff_who_are_not_superusers_are_refused(self):
        """Whoever gets in becomes the store's administrator."""
        response = self.post({"username": "clerk", "password": "right"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["external_user_identifier"], "")

    def test_an_ordinary_user_is_refused(self):
        response = self.post({"username": "plain", "password": "right"})
        self.assertEqual(response.status_code, 401)

    def test_an_inactive_superuser_is_refused(self):
        self.root.is_active = False
        self.root.save(update_fields=["is_active"])
        self.assertEqual(self.post(BODY).status_code, 401)

    def test_a_request_through_the_public_name_is_refused(self):
        """The second layer under nginx's deny: the store calls us as
        web-<name>:8000, never as the platform's public host.

        ALLOWED_HOSTS is widened here on purpose. On a real deployment the
        public name IS allowed — that is the name the site answers on — so
        the interesting request is one that passes Django's host check and
        still has to be turned away. Leaving it narrow would make this test
        pass on a 400 from CommonMiddleware and prove nothing about the view.
        """
        with override_settings(ALLOWED_HOSTS=["*"]):
            response = self.post(BODY, HTTP_HOST="portal.example.com")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["external_user_identifier"], "")

    def test_the_compose_alias_is_allowed(self):
        with override_settings(ALLOWED_HOSTS=["*"]):
            response = self.post(BODY, HTTP_HOST="web-zenobia:8000")
        self.assertEqual(response.status_code, 200)

    def test_a_malformed_body_is_a_400_with_the_same_shape(self):
        response = self.client.post(reverse("sso:lakefs_auth"), data="not json",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["external_user_identifier"], "")

    def test_get_is_not_a_login(self):
        self.assertEqual(self.client.get(reverse("sso:lakefs_auth")).status_code, 405)

    @override_settings(LAKEFS_ENABLED=False)
    def test_a_host_without_the_store_has_no_such_door(self):
        self.assertEqual(self.post(BODY).status_code, 404)

    @override_settings(LAKEFS_ADMIN_USERNAME="")
    def test_an_empty_identifier_refuses_rather_than_admitting_nobody(self):
        """A 200 with "" would be a login that fails at the far end with no
        message worth having; refuse here and log it."""
        self.assertEqual(self.post(BODY).status_code, 401)
