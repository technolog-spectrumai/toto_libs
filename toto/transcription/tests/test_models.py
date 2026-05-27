from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.transcription.models import (
    TranscriptAccessMode,
    TranscriptCollection,
    TranscriptSource,
    TranscriptionJob,
    TranscriptSegment,
    TranscriptSpeaker,
    _format_ms,
)

User = get_user_model()


class FormatMsTests(TestCase):
    def test_zero(self):
        assert _format_ms(0) == "00:00.000"

    def test_seconds_millis(self):
        assert _format_ms(61_500) == "01:01.500"

    def test_hours(self):
        assert _format_ms(3_661_000) == "01:01:01.000"

    def test_none_treated_as_zero(self):
        assert _format_ms(None) == "00:00.000"


class TranscriptCollectionAccessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.reader = User.objects.create_user(username="reader", password="pw")
        self.stranger = User.objects.create_user(username="stranger", password="pw")
        self.superuser = User.objects.create_superuser(username="super", password="pw")
        self.public_col = TranscriptCollection.objects.create(
            title="Public", slug="public-col", access_mode=TranscriptAccessMode.PUBLIC, owner=self.owner
        )
        self.private_col = TranscriptCollection.objects.create(
            title="Private", slug="private-col", access_mode=TranscriptAccessMode.PRIVATE, owner=self.owner
        )
        self.private_col.readers.add(self.reader)

    def test_public_readable_by_anyone(self):
        assert self.public_col.user_can_read(None) is True
        assert self.public_col.user_can_read(self.stranger) is True

    def test_private_not_readable_by_anonymous(self):
        assert self.private_col.user_can_read(None) is False

    def test_private_readable_by_owner(self):
        assert self.private_col.user_can_read(self.owner) is True

    def test_private_readable_by_reader(self):
        assert self.private_col.user_can_read(self.reader) is True

    def test_private_not_readable_by_stranger(self):
        assert self.private_col.user_can_read(self.stranger) is False

    def test_superuser_can_read_private(self):
        assert self.private_col.user_can_read(self.superuser) is True

    def test_owner_can_write(self):
        assert self.private_col.user_can_write(self.owner) is True

    def test_reader_cannot_write(self):
        assert self.private_col.user_can_write(self.reader) is False

    def test_anonymous_cannot_write(self):
        assert self.private_col.user_can_write(None) is False

    def test_is_private_flag(self):
        assert self.private_col.is_private is True
        assert self.public_col.is_private is False

    def test_slug_auto_generated(self):
        col = TranscriptCollection.objects.create(title="Auto Slug Test", owner=self.owner)
        assert col.slug == "auto-slug-test"


class TranscriptSourcePropertyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="src_owner", password="pw")
        from unittest.mock import MagicMock, patch
        from toto.vault.models import Bucket, VaultFile
        self.bucket = Bucket.objects.create(name="Test Bucket", slug="test-bucket", owner=self.owner)
        self.collection = TranscriptCollection.objects.create(
            title="Src Collection", slug="src-col", owner=self.owner
        )

    def _make_vault_file(self, file_type="audio"):
        from django.core.files.base import ContentFile
        from toto.vault.models import VaultFile
        return VaultFile.objects.create(
            owner=self.owner,
            title="test audio",
            bucket=self.bucket,
            file=ContentFile(b"data", name="test.mp3"),
            file_type=file_type,
            is_encrypted=False,
        )

    def test_has_transcript_with_text(self):
        vf = self._make_vault_file()
        source = TranscriptSource.objects.create(
            collection=self.collection, source_file=vf, title="T", slug="t", transcript_text="hello"
        )
        assert source.has_transcript is True

    def test_has_transcript_false_when_empty(self):
        vf = self._make_vault_file()
        source = TranscriptSource.objects.create(
            collection=self.collection, source_file=vf, title="T2", slug="t2"
        )
        assert source.has_transcript is False

    def test_is_audio_source(self):
        vf = self._make_vault_file(file_type="audio")
        source = TranscriptSource.objects.create(
            collection=self.collection, source_file=vf, title="A", slug="a"
        )
        assert source.is_audio_source is True
        assert source.is_video_source is False

    def test_is_video_source(self):
        vf = self._make_vault_file(file_type="video")
        source = TranscriptSource.objects.create(
            collection=self.collection, source_file=vf, title="V", slug="v"
        )
        assert source.is_video_source is True
        assert source.is_audio_source is False

    def test_slug_auto_generated(self):
        vf = self._make_vault_file()
        source = TranscriptSource.objects.create(
            collection=self.collection, source_file=vf, title="Auto Slug Source"
        )
        assert source.slug == "auto-slug-source"


class TranscriptionJobTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="job_owner", password="pw")
        from toto.vault.models import Bucket, VaultFile
        from django.core.files.base import ContentFile
        bucket = Bucket.objects.create(name="Job Bucket", slug="job-bucket", owner=self.owner)
        col = TranscriptCollection.objects.create(title="Job Col", slug="job-col", owner=self.owner)
        vf = VaultFile.objects.create(
            owner=self.owner, title="j", bucket=bucket,
            file=ContentFile(b"x", name="j.mp3"), file_type="audio", is_encrypted=False,
        )
        self.source = TranscriptSource.objects.create(collection=col, source_file=vf, title="J", slug="j")

    def test_str(self):
        job = TranscriptionJob.objects.create(source=self.source, status=TranscriptionJob.Status.QUEUED)
        assert "Queued" in str(job)

    def test_duration_label_empty_without_timestamps(self):
        job = TranscriptionJob.objects.create(source=self.source)
        assert job.duration_label == ""

    def test_duration_label_with_timestamps(self):
        from django.utils import timezone
        from datetime import timedelta
        job = TranscriptionJob.objects.create(source=self.source)
        job.started_at = timezone.now()
        job.finished_at = job.started_at + timedelta(seconds=42)
        assert job.duration_label == "42s"
