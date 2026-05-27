from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .models import VodAccessGrant, VodAccessMode, VodCollection, VodPlaybackEvent, VodVideo, VodVideoAccessMode
from .services import can_access_video, user_is_vod_manager

User = get_user_model()


def make_user(**kwargs):
    defaults = {"username": "user", "password": "pass"}
    defaults.update(kwargs)
    return User.objects.create_user(**defaults)


def make_collection(**kwargs):
    defaults = {"title": "Test Collection", "access_mode": VodAccessMode.PUBLIC}
    defaults.update(kwargs)
    return VodCollection.objects.create(**defaults)


def make_video(collection, **kwargs):
    from unittest.mock import MagicMock

    source = MagicMock()
    source.pk = 1
    source.file_type = "video"
    source.is_encrypted = False

    defaults = {
        "title": "Test Video",
        "status": VodVideo.Status.PUBLISHED,
        "access_mode": VodVideoAccessMode.INHERIT,
    }
    defaults.update(kwargs)
    # Create a real VaultFile stub via raw SQL to avoid vault dependency in tests
    from django.apps import apps
    VaultFile = apps.get_model("vault", "VaultFile")
    Bucket = apps.get_model("vault", "Bucket")
    owner = User.objects.filter(is_superuser=True).first() or User.objects.order_by("id").first()
    if owner is None:
        owner = make_user(username="vf_owner", is_staff=True)
    bucket, _ = Bucket.objects.get_or_create(slug="test-vod-bucket", defaults={"name": "Test VOD", "owner": owner})
    vf, _ = VaultFile.objects.get_or_create(
        title="test-source",
        defaults={"owner": owner, "bucket": bucket, "file_type": "video", "is_public": True, "is_encrypted": False},
    )
    defaults["source_file"] = vf
    return VodVideo.objects.create(collection=collection, **defaults)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class VodCollectionModelTests(TestCase):
    def test_slug_is_generated(self):
        collection = VodCollection.objects.create(title="Industrial Robotics")
        self.assertEqual(collection.slug, "industrial-robotics")

    def test_public_collection_is_listed(self):
        collection = VodCollection.objects.create(title="Robot Footage", access_mode=VodAccessMode.PUBLIC)
        self.assertTrue(collection.is_publicly_listed)

    def test_non_public_collection_not_listed(self):
        collection = VodCollection.objects.create(title="Private Footage", access_mode=VodAccessMode.STAFF)
        self.assertFalse(collection.is_publicly_listed)

    def test_get_absolute_url(self):
        collection = VodCollection.objects.create(title="My Collection")
        self.assertIn("my-collection", collection.get_absolute_url())


# ---------------------------------------------------------------------------
# user_is_vod_manager tests
# ---------------------------------------------------------------------------

class VodManagerTests(TestCase):
    def setUp(self):
        self.staff = make_user(username="staff", is_staff=True)
        self.owner = make_user(username="owner")
        self.other = make_user(username="other")
        self.collection = make_collection(owner=self.owner)

    def test_anonymous_is_not_manager(self):
        self.assertFalse(user_is_vod_manager(None))

    def test_staff_is_manager(self):
        self.assertTrue(user_is_vod_manager(self.staff))

    def test_owner_is_manager_for_own_collection(self):
        self.assertTrue(user_is_vod_manager(self.owner, self.collection))

    def test_owner_not_manager_without_collection(self):
        self.assertFalse(user_is_vod_manager(self.owner))

    def test_other_user_not_manager(self):
        self.assertFalse(user_is_vod_manager(self.other, self.collection))


# ---------------------------------------------------------------------------
# can_access_video tests
# ---------------------------------------------------------------------------

class CanAccessVideoTests(TestCase):
    def setUp(self):
        self.staff = make_user(username="staff2", is_staff=True)
        self.user = make_user(username="regular")
        self.collection = make_collection(access_mode=VodAccessMode.PUBLIC)

    def _video(self, **kwargs):
        return make_video(self.collection, **kwargs)

    def test_public_video_allowed_for_anonymous(self):
        video = self._video(access_mode=VodVideoAccessMode.INHERIT)
        rf = RequestFactory()

        class AnonUser:
            is_authenticated = False

        decision = can_access_video(AnonUser(), video)
        self.assertTrue(decision.allowed)

    def test_unpublished_video_denied_for_regular_user(self):
        video = self._video(status=VodVideo.Status.DRAFT)
        decision = can_access_video(self.user, video)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "not-published")

    def test_unpublished_video_allowed_for_staff(self):
        video = self._video(status=VodVideo.Status.DRAFT)
        decision = can_access_video(self.staff, video)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "manager-preview")

    def test_staff_only_video_denied_for_regular_user(self):
        collection = make_collection(access_mode=VodAccessMode.STAFF)
        video = make_video(collection, access_mode=VodVideoAccessMode.INHERIT)
        decision = can_access_video(self.user, video)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "staff-only")

    def test_staff_only_video_allowed_for_staff(self):
        collection = make_collection(access_mode=VodAccessMode.STAFF)
        video = make_video(collection, access_mode=VodVideoAccessMode.INHERIT)
        decision = can_access_video(self.staff, video)
        self.assertTrue(decision.allowed)

    def test_login_required_for_subscriber_gated_anonymous(self):
        collection = make_collection(access_mode=VodAccessMode.SUBSCRIBERS)
        video = make_video(collection, access_mode=VodVideoAccessMode.INHERIT)

        class AnonUser:
            is_authenticated = False

        decision = can_access_video(AnonUser(), video)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "login-required")

    def test_subscriber_gated_without_plan_returns_missing_plan(self):
        collection = make_collection(access_mode=VodAccessMode.SUBSCRIBERS)
        video = make_video(collection, access_mode=VodVideoAccessMode.INHERIT)
        decision = can_access_video(self.user, video)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "missing-required-plan")

    def test_invoice_gated_without_grant_denied(self):
        collection = make_collection(access_mode=VodAccessMode.INVOICE)
        video = make_video(collection, access_mode=VodVideoAccessMode.INHERIT)
        decision = can_access_video(self.user, video)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "invoice-required")

    def test_invoice_gated_with_active_grant_allowed(self):
        collection = make_collection(access_mode=VodAccessMode.INVOICE)
        video = make_video(collection, access_mode=VodVideoAccessMode.INHERIT)
        VodAccessGrant.objects.create(
            user=self.user,
            video=video,
            status=VodAccessGrant.Status.ACTIVE,
        )
        decision = can_access_video(self.user, video)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "invoice-paid")

    def test_unlisted_video_allowed(self):
        collection = make_collection(access_mode=VodAccessMode.UNLISTED)
        video = make_video(collection, access_mode=VodVideoAccessMode.INHERIT)

        class AnonUser:
            is_authenticated = False

        decision = can_access_video(AnonUser(), video)
        self.assertTrue(decision.allowed)


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------

class VodViewTests(TestCase):
    def setUp(self):
        self.staff = make_user(username="staff3", is_staff=True)
        self.user = make_user(username="viewer")
        self.collection = make_collection(title="Public Coll", access_mode=VodAccessMode.PUBLIC)

    def test_collection_list_200(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("vod:collection_list"))
        self.assertEqual(resp.status_code, 200)

    def test_collection_list_shows_public_to_anonymous(self):
        resp = self.client.get(reverse("vod:collection_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Public Coll")

    def test_collection_detail_200(self):
        resp = self.client.get(reverse("vod:collection_detail", args=[self.collection.slug]))
        self.assertEqual(resp.status_code, 200)

    def test_video_detail_200_for_public_video(self):
        video = make_video(self.collection)
        resp = self.client.get(reverse("vod:video_detail", args=[self.collection.slug, video.slug]))
        self.assertEqual(resp.status_code, 200)

    def test_collection_create_requires_staff(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("vod:collection_create"))
        self.assertEqual(resp.status_code, 403)

    def test_collection_create_accessible_to_staff(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("vod:collection_create"))
        self.assertEqual(resp.status_code, 200)

    def test_upload_requires_login(self):
        resp = self.client.get(reverse("vod:upload"))
        self.assertNotEqual(resp.status_code, 200)

    def test_upload_requires_staff(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("vod:upload"))
        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# Playback event API tests
# ---------------------------------------------------------------------------

class PlaybackEventApiTests(TestCase):
    def setUp(self):
        self.user = make_user(username="watcher")
        self.collection = make_collection(access_mode=VodAccessMode.PUBLIC)
        self.video = make_video(self.collection)

    def test_event_api_returns_ok_for_public_video(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse("vod:playback_event_api", args=[self.collection.slug, self.video.slug]),
            {"event": "play", "seconds_watched": "0"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

    def test_event_api_denied_for_subscriber_gated(self):
        collection = make_collection(access_mode=VodAccessMode.SUBSCRIBERS)
        video = make_video(collection)
        resp = self.client.post(
            reverse("vod:playback_event_api", args=[collection.slug, video.slug]),
            {"event": "play"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["ok"])
