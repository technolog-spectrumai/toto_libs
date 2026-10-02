"""Your account (2026-09-30; on your own profile's tabs since 2026-10-02,
stage 50): the profile and time zone sections, the top-bar entry, the
per-request time zone and the audit record they leave.

Own account only by construction — the views take no person — so "cannot edit
another's" is pinned by posting another member's identifiers and watching
them be ignored.
"""

from __future__ import annotations

import io
import shutil
import tempfile
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.audit.models import AuditRecord
from toto.core.middleware import ProfileTimezoneMiddleware
from toto.core.models import Platform
from toto.people.models import Person, validate_time_zone

User = get_user_model()


def picture(fmt="PNG", size=(8, 8), name="me.png", content_type="image/png"):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)


class AccountTestCase(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        media = override_settings(MEDIA_ROOT=self.media)
        media.enable()
        self.addCleanup(media.disable)
        Platform.objects.create(site_name="Test", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("ada", password="pw")
        self.person = Person.objects.create(user=self.user, display_name="Ada",
                                            bio="old bio", phone="111")
        self.profile_url = reverse("socialhub:profile_details", args=[self.person.slug])
        self.client.force_login(self.user)

    def post_profile(self, **data):
        payload = {"display_name": self.person.display_name, "bio": self.person.bio or "",
                   "phone": self.person.phone or ""}
        payload.update(data)
        return self.client.post(reverse("account:profile"), payload)

    def records(self):
        return AuditRecord.objects.filter(action="SOCIALHUB.PROFILE_CHANGED")


class PageTests(AccountTestCase):
    def test_the_page_and_its_top_bar_entry(self):
        # /account/ stays, and leads to the member's own profile (stage 50).
        self.assertEqual(reverse("account:home"), "/account/")
        response = self.client.get("/account/")
        self.assertRedirects(response, self.profile_url, fetch_redirect_response=False)
        response = self.client.get(self.profile_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="header-profile"')
        # The profile form on the Edit profile tab, the time zone on Account.
        response = self.client.get(self.profile_url + "?tab=edit")
        self.assertContains(response, 'action="/account/profile/"')
        response = self.client.get(self.profile_url + "?tab=account")
        self.assertContains(response, 'action="/account/timezone/"')

    def test_signed_out_is_sent_to_sign_in(self):
        self.client.logout()
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("/account/", response["Location"].split("?")[0])

    def test_a_member_with_no_person_yet_gets_one_on_the_first_save(self):
        bob = User.objects.create_user("bob", password="pw")
        self.client.force_login(bob)
        # No profile to go to: the page is drawn at /account/, Edit profile
        # its first tab.
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'action="/account/profile/"')
        self.assertContains(response, "Your profile is created when you first save it.")
        self.assertFalse(Person.objects.filter(user=bob).exists())
        response = self.client.post(reverse("account:profile"),
                                    {"display_name": "Bob", "bio": "", "phone": ""})
        person = Person.objects.get(user=bob)
        self.assertEqual(person.display_name, "Bob")
        # ...and the first save goes to the profile it made, on its form.
        self.assertRedirects(response, reverse("socialhub:profile_details", args=[person.slug])
                             + "?tab=edit#profile", fetch_redirect_response=False)

    def test_the_page_is_free_on_every_plan(self):
        from toto.subscriptions.gate import ALWAYS_FREE

        # The doors are account's, the page (the profile) the socialhub's.
        self.assertIn("account", ALWAYS_FREE)
        self.assertIn("socialhub", ALWAYS_FREE)


class ProfileTests(AccountTestCase):
    def test_edit_own_profile(self):
        response = self.post_profile(display_name="Ada L.", bio="new bio", phone="+48 600")
        self.assertRedirects(response, self.profile_url + "?tab=edit#profile",
                             fetch_redirect_response=False)
        self.person.refresh_from_db()
        self.assertEqual((self.person.display_name, self.person.bio, self.person.phone),
                         ("Ada L.", "new bio", "+48 600"))

    def test_a_blank_display_name_is_refused(self):
        response = self.post_profile(display_name="   ")
        self.assertEqual(response.status_code, 400)
        self.person.refresh_from_db()
        self.assertEqual(self.person.display_name, "Ada")

    def test_cannot_edit_another_members_profile(self):
        eve = Person.objects.create(user=User.objects.create_user("eve", password="pw"),
                                    display_name="Eve", bio="eve's", phone="999")
        response = self.post_profile(display_name="Hacked", user=eve.user_id, slug=eve.slug,
                                     pk=eve.pk, id=eve.pk, person=eve.slug)
        self.assertEqual(response.status_code, 302)
        eve.refresh_from_db()
        self.assertEqual((eve.display_name, eve.bio, eve.phone), ("Eve", "eve's", "999"))
        self.person.refresh_from_db()
        self.assertEqual(self.person.display_name, "Hacked")
        self.assertEqual(self.person.user_id, self.user.pk)

    def test_fields_that_decide_access_do_not_ride_along(self):
        response = self.post_profile(location_sharing="exact",
                                     timezone="Asia/Tokyo")
        self.assertEqual(response.status_code, 302)
        self.person.refresh_from_db()
        self.assertEqual(self.person.location_sharing, "off")
        self.assertEqual(self.person.timezone, "")

    def test_the_get_is_refused_on_the_write_doors(self):
        self.assertEqual(self.client.get(reverse("account:profile")).status_code, 405)
        self.assertEqual(self.client.get(reverse("account:timezone")).status_code, 405)


class AvatarTests(AccountTestCase):
    def test_a_png_is_stored_under_a_name_of_ours(self):
        response = self.post_profile(avatar=picture(name="IMG holiday <b>.png"))
        self.assertEqual(response.status_code, 302)
        self.person.refresh_from_db()
        self.assertTrue(self.person.avatar.name.startswith("avatars/"))
        self.assertTrue(self.person.avatar.name.endswith(".png"))
        self.assertNotIn("holiday", self.person.avatar.name)

    def test_a_picture_named_like_a_page_is_refused(self):
        response = self.post_profile(avatar=picture(name="me.html"))
        self.assertEqual(response.status_code, 400)
        self.person.refresh_from_db()
        self.assertFalse(self.person.avatar)

    def test_the_extension_follows_the_content_not_the_name(self):
        self.post_profile(avatar=picture(fmt="JPEG", name="me.png"))
        self.person.refresh_from_db()
        self.assertTrue(self.person.avatar.name.endswith(".jpg"))

    def test_something_that_is_not_a_picture_is_refused(self):
        upload = SimpleUploadedFile("me.png", b"<html><script>alert(1)</script></html>",
                                    content_type="image/png")
        response = self.post_profile(avatar=upload)
        self.assertEqual(response.status_code, 400)
        self.person.refresh_from_db()
        self.assertFalse(self.person.avatar)

    def test_a_picture_format_outside_the_list_is_refused(self):
        response = self.post_profile(avatar=picture(fmt="BMP", name="me.bmp",
                                                    content_type="image/bmp"))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "JPEG, PNG, GIF or WebP", status_code=400)

    @override_settings(SOCIALHUB_AVATAR_MAX_BYTES=10)
    def test_a_picture_over_the_cap_is_refused(self):
        response = self.post_profile(avatar=picture())
        self.assertEqual(response.status_code, 400)
        self.person.refresh_from_db()
        self.assertFalse(self.person.avatar)

    def test_a_picture_too_many_pixels_wide_is_refused(self):
        response = self.post_profile(avatar=picture(size=(5000, 1)))
        self.assertEqual(response.status_code, 400)

    @override_settings(VAULT_REFUSED_FILE_TYPES={"image"})
    def test_a_host_refusing_images_refuses_avatars(self):
        response = self.post_profile(avatar=picture())
        self.assertEqual(response.status_code, 400)

    def test_the_antivirus_door_is_asked_and_its_refusal_holds(self):
        from unittest import mock

        from toto.vault.scanning import Verdict

        refused = Verdict.refused("active-content", "Something hostile.")
        with mock.patch("toto.vault.scanning.scan", return_value=refused) as scan:
            response = self.post_profile(avatar=picture())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(scan.call_args.kwargs["file_type"], "image")
        self.person.refresh_from_db()
        self.assertFalse(self.person.avatar)

    def test_a_replaced_or_cleared_avatar_leaves_the_disk(self):
        self.post_profile(avatar=picture())
        self.person.refresh_from_db()
        first = self.person.avatar.name
        storage = self.person.avatar.storage
        self.assertTrue(storage.exists(first))
        self.post_profile(avatar=picture(fmt="GIF", name="b.gif", content_type="image/gif"))
        self.person.refresh_from_db()
        self.assertFalse(storage.exists(first))
        second = self.person.avatar.name
        self.post_profile(**{"avatar-clear": "on"})
        self.person.refresh_from_db()
        self.assertFalse(self.person.avatar)
        self.assertFalse(storage.exists(second))


class TimeZoneTests(AccountTestCase):
    def test_choosing_a_zone(self):
        response = self.client.post(reverse("account:timezone"), {"timezone": "Europe/Warsaw"})
        self.assertRedirects(response, self.profile_url + "?tab=account#timezone",
                             fetch_redirect_response=False)
        self.person.refresh_from_db()
        self.assertEqual(self.person.timezone, "Europe/Warsaw")

    def test_blank_is_the_platform_default(self):
        self.person.timezone = "Asia/Tokyo"
        self.person.save()
        self.client.post(reverse("account:timezone"), {"timezone": ""})
        self.person.refresh_from_db()
        self.assertEqual(self.person.timezone, "")

    def test_an_invalid_zone_is_refused(self):
        for bad in ("Mars/Olympus", "../../etc/passwd", "europe/warsaw", "x" * 80):
            response = self.client.post(reverse("account:timezone"), {"timezone": bad})
            self.assertEqual(response.status_code, 400, bad)
        self.person.refresh_from_db()
        self.assertEqual(self.person.timezone, "")

    def test_the_model_validates_the_name_too(self):
        validate_time_zone("")
        validate_time_zone("America/New_York")
        with self.assertRaises(ValidationError):
            validate_time_zone("Mars/Olympus")
        self.person.timezone = "Mars/Olympus"
        with self.assertRaises(ValidationError):
            self.person.full_clean()

    def test_pages_show_times_in_the_chosen_zone(self):
        self.client.post(reverse("account:timezone"), {"timezone": "Asia/Tokyo"})
        response = self.client.get("/account/?tab=account", follow=True)
        self.assertContains(response, "(Asia/Tokyo)")
        self.assertEqual(timezone.get_current_timezone_name(), "UTC")  # deactivated after


class MiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user("kai", password="pw")
        self.person = Person.objects.create(user=self.user, display_name="Kai")

    def render(self, user):
        seen = {}

        def view(request):
            moment = datetime(2026, 1, 15, 12, 0, tzinfo=dt_timezone.utc)
            seen["zone"] = timezone.get_current_timezone_name()
            seen["local"] = timezone.localtime(moment).strftime("%H:%M")
            return HttpResponse("ok")

        request = self.factory.get("/")
        request.user = user
        ProfileTimezoneMiddleware(view)(request)
        return seen

    @override_settings(TIME_ZONE="UTC")
    def test_the_members_zone_is_active_for_the_request_only(self):
        self.person.timezone = "Europe/Warsaw"
        self.person.save()
        seen = self.render(self.user)
        self.assertEqual(seen, {"zone": "Europe/Warsaw", "local": "13:00"})
        self.assertEqual(timezone.get_current_timezone_name(), "UTC")

    @override_settings(TIME_ZONE="UTC")
    def test_blank_anonymous_and_no_person_get_the_default(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(self.render(self.user)["local"], "12:00")
        self.assertEqual(self.render(AnonymousUser())["zone"], "UTC")
        loner = User.objects.create_user("loner", password="pw")
        self.assertEqual(self.render(loner)["zone"], "UTC")

    @override_settings(TIME_ZONE="UTC")
    def test_a_stale_zone_name_falls_back_rather_than_failing(self):
        Person.objects.filter(pk=self.person.pk).update(timezone="Gone/Away")
        self.user.refresh_from_db()
        self.assertEqual(self.render(User.objects.get(pk=self.user.pk))["zone"], "UTC")

    def test_the_host_runs_it(self):
        from django.conf import settings

        middleware = settings.MIDDLEWARE
        self.assertIn("toto.core.middleware.ProfileTimezoneMiddleware", middleware)
        self.assertGreater(middleware.index("toto.core.middleware.ProfileTimezoneMiddleware"),
                           middleware.index("django.contrib.auth.middleware.AuthenticationMiddleware"))


class AuditTests(AccountTestCase):
    def test_a_profile_change_names_the_fields_not_the_values(self):
        self.post_profile(display_name="Ada L.", phone="+48 600 700 800")
        record = self.records().get()
        self.assertEqual(record.metadata["fields"], ["display_name", "phone"])
        self.assertEqual(record.object_id, str(self.person.pk))
        self.assertEqual(record.actor_user_id, self.user.pk)
        self.assertEqual(record.changes, {})
        self.assertNotIn("+48 600 700 800", str(record.metadata))

    def test_an_avatar_and_a_time_zone_are_recorded(self):
        self.post_profile(avatar=picture())
        self.client.post(reverse("account:timezone"), {"timezone": "Europe/Warsaw"})
        fields = [r.metadata["fields"] for r in self.records().order_by("sequence")]
        self.assertEqual(fields, [["avatar"], ["timezone"]])
        self.assertNotIn("Europe/Warsaw", str(self.records().last().metadata))

    def test_saving_nothing_new_records_nothing(self):
        self.post_profile()
        self.client.post(reverse("account:timezone"), {"timezone": ""})
        self.assertFalse(self.records().exists())

    def test_a_refused_change_records_nothing(self):
        self.client.post(reverse("account:timezone"), {"timezone": "Mars/Olympus"})
        self.post_profile(display_name="")
        self.assertFalse(self.records().exists())
