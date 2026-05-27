from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.transcription.models import (
    TranscriptAccessMode,
    TranscriptCollection,
    TranscriptSegment,
    TranscriptSource,
    TranscriptionJob,
    TranscriptSpeaker,
)
from toto.transcription.services import (
    AccessDecision,
    can_access_source,
    export_transcript,
    readable_collections_for_user,
    readable_sources_for_user,
    transcript_to_json,
    transcript_to_srt,
    transcript_to_text,
    transcript_to_vtt,
    user_can_create_collections,
    user_is_transcription_manager,
    writable_collections_for_user,
    _hash_ip,
    ms_to_srt_time,
    ms_to_vtt_time,
)

User = get_user_model()


def _vault_file(owner, bucket, file_type="audio", name="test.mp3"):
    from django.core.files.base import ContentFile
    from toto.vault.models import VaultFile
    return VaultFile.objects.create(
        owner=owner, title=name, bucket=bucket,
        file=ContentFile(b"data", name=name),
        file_type=file_type, is_encrypted=False,
    )


def _bucket(owner, slug="svc-bucket"):
    from toto.vault.models import Bucket
    return Bucket.objects.create(name="Svc Bucket", slug=slug, owner=owner)


class UserPermissionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="staff_svc", password="pw", is_staff=True)
        self.regular = User.objects.create_user(username="regular_svc", password="pw")
        self.superuser = User.objects.create_superuser(username="super_svc", password="pw")

    def test_staff_can_create_collections(self):
        assert user_can_create_collections(self.staff) is True

    def test_superuser_can_create_collections(self):
        assert user_can_create_collections(self.superuser) is True

    def test_anonymous_cannot_create(self):
        assert user_can_create_collections(None) is False

    def test_regular_user_can_create_by_default(self):
        # TRANSCRIPTION_ALLOW_USER_COLLECTIONS defaults to True
        assert user_can_create_collections(self.regular) is True

    def test_manager_superuser(self):
        assert user_is_transcription_manager(self.superuser) is True

    def test_manager_anonymous(self):
        assert user_is_transcription_manager(None) is False


class ReadableCollectionsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="rc_owner", password="pw")
        self.other = User.objects.create_user(username="rc_other", password="pw")
        self.public_col = TranscriptCollection.objects.create(
            title="RC Public", slug="rc-public", access_mode=TranscriptAccessMode.PUBLIC, owner=self.owner
        )
        self.private_col = TranscriptCollection.objects.create(
            title="RC Private", slug="rc-private", access_mode=TranscriptAccessMode.PRIVATE, owner=self.owner
        )

    def test_anonymous_sees_public_only(self):
        qs = readable_collections_for_user(None)
        slugs = list(qs.values_list("slug", flat=True))
        assert "rc-public" in slugs
        assert "rc-private" not in slugs

    def test_owner_sees_private(self):
        qs = readable_collections_for_user(self.owner)
        slugs = list(qs.values_list("slug", flat=True))
        assert "rc-private" in slugs

    def test_other_user_does_not_see_private(self):
        qs = readable_collections_for_user(self.other)
        slugs = list(qs.values_list("slug", flat=True))
        assert "rc-private" not in slugs

    def test_writable_for_owner(self):
        qs = writable_collections_for_user(self.owner)
        assert qs.filter(slug="rc-private").exists()

    def test_writable_empty_for_other(self):
        qs = writable_collections_for_user(self.other)
        assert not qs.exists()


class CanAccessSourceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="cas_owner", password="pw")
        self.reader = User.objects.create_user(username="cas_reader", password="pw")
        bucket = _bucket(self.owner, slug="cas-bucket")
        self.col = TranscriptCollection.objects.create(
            title="CAS Col", slug="cas-col", access_mode=TranscriptAccessMode.PUBLIC, owner=self.owner
        )
        vf = _vault_file(self.owner, bucket)
        self.source = TranscriptSource.objects.create(
            collection=self.col, source_file=vf, title="CAS Src", slug="cas-src",
            status=TranscriptSource.Status.TRANSCRIBED,
        )
        self.draft_source = TranscriptSource.objects.create(
            collection=self.col, source_file=_vault_file(self.owner, bucket, name="d.mp3"),
            title="Draft", slug="draft-src", status=TranscriptSource.Status.DRAFT,
        )
        self.archived_source = TranscriptSource.objects.create(
            collection=self.col, source_file=_vault_file(self.owner, bucket, name="ar.mp3"),
            title="Archived", slug="archived-src", status=TranscriptSource.Status.ARCHIVED,
        )

    def test_transcribed_public_accessible_by_anon(self):
        decision = can_access_source(None, self.source)
        assert decision.allowed is True

    def test_draft_not_accessible_by_anon(self):
        decision = can_access_source(None, self.draft_source)
        assert decision.allowed is False

    def test_archived_not_accessible_by_anon(self):
        decision = can_access_source(None, self.archived_source)
        assert decision.allowed is False

    def test_owner_can_access_draft(self):
        decision = can_access_source(self.owner, self.draft_source)
        assert decision.allowed is True

    def test_owner_can_access_archived(self):
        decision = can_access_source(self.owner, self.archived_source)
        assert decision.allowed is True


class ExportFormatsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="exp_owner", password="pw")
        bucket = _bucket(self.owner, slug="exp-bucket")
        col = TranscriptCollection.objects.create(
            title="Exp Col", slug="exp-col", access_mode=TranscriptAccessMode.PUBLIC, owner=self.owner
        )
        vf = _vault_file(self.owner, bucket)
        self.source = TranscriptSource.objects.create(
            collection=col, source_file=vf, title="Exp Src", slug="exp-src",
            status=TranscriptSource.Status.TRANSCRIBED,
        )
        job = TranscriptionJob.objects.create(source=self.source, status=TranscriptionJob.Status.SUCCESS)
        for i, text in enumerate(["Hello world.", "This is a test."]):
            TranscriptSegment.objects.create(
                job=job, source=self.source, index=i + 1,
                start_ms=i * 3000, end_ms=(i + 1) * 3000, text=text,
            )

    def test_transcript_to_text(self):
        result = transcript_to_text(self.source)
        assert "Hello world." in result
        assert "This is a test." in result

    def test_transcript_to_srt(self):
        result = transcript_to_srt(self.source)
        assert "00:00:00,000 --> 00:00:03,000" in result
        assert "Hello world." in result

    def test_transcript_to_vtt(self):
        result = transcript_to_vtt(self.source)
        assert "WEBVTT" in result
        assert "00:00:00.000 --> 00:00:03.000" in result

    def test_transcript_to_json(self):
        import json
        result = transcript_to_json(self.source)
        data = json.loads(result)
        assert data["source"]["slug"] == "exp-src"
        assert len(data["segments"]) == 2
        assert data["segments"][0]["text"] == "Hello world."

    def test_export_txt(self):
        content, ct, fname = export_transcript(self.source, "txt")
        assert "text/plain" in ct
        assert fname.endswith(".txt")

    def test_export_srt(self):
        content, ct, fname = export_transcript(self.source, "srt")
        assert fname.endswith(".srt")

    def test_export_vtt(self):
        content, ct, fname = export_transcript(self.source, "vtt")
        assert fname.endswith(".vtt")

    def test_export_json(self):
        content, ct, fname = export_transcript(self.source, "json")
        assert "application/json" in ct
        assert fname.endswith(".json")

    def test_export_invalid_raises(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            export_transcript(self.source, "xyz")


class TimingHelperTests(TestCase):
    def test_srt_time(self):
        assert ms_to_srt_time(0) == "00:00:00,000"
        assert ms_to_srt_time(61_500) == "00:01:01,500"
        assert ms_to_srt_time(3_661_000) == "01:01:01,000"

    def test_vtt_time(self):
        assert ms_to_vtt_time(0) == "00:00:00.000"
        assert ms_to_vtt_time(61_500) == "00:01:01.500"

    def test_hash_ip_stable(self):
        h1 = _hash_ip("1.2.3.4")
        h2 = _hash_ip("1.2.3.4")
        assert h1 == h2
        assert len(h1) == 64

    def test_hash_ip_empty(self):
        assert _hash_ip("") == ""
