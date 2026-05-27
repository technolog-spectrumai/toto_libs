"""
Management command to pre-seed the Transcription app with demo data.

Usage:
    python manage.py seed_transcription
    python manage.py seed_transcription --user admin
    python manage.py seed_transcription --reset

Creates:
  • 1 public TranscriptCollection  ("Demo Transcriptions")
  • 3 TranscriptSources with sidecar .txt transcripts
  • Runs transcription via the sidecar_txt_backend so segments are stored
  • Idempotent: skips objects whose slugs already exist (unless --reset)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()

_COLLECTION_SLUG = "demo-transcriptions"

_SOURCES = [
    {
        "slug": "demo-intro-speech",
        "title": "Intro Speech",
        "language": "en",
        "transcript": (
            "Welcome to the Toto platform. This is a short demo of the transcription feature.\n"
            "You can upload audio or video files and transcribe them using the Whisper engine.\n"
            "Transcripts are stored as timestamped segments and can be exported to TXT, SRT, VTT, or JSON."
        ),
    },
    {
        "slug": "demo-product-overview",
        "title": "Product Overview",
        "language": "en",
        "transcript": (
            "The product overview begins here.\n"
            "Toto is a modular, self-hosted platform designed for communities, studios, and organizations.\n"
            "It ships with vault storage, identity management, kanban, events, and now — transcription.\n"
            "All components share a common design system and authentication layer."
        ),
    },
    {
        "slug": "demo-qa-session",
        "title": "Q&A Session",
        "language": "en",
        "transcript": (
            "Question: How does the transcription engine work?\n"
            "Answer: By default it uses faster-whisper running locally on CPU. "
            "You can switch to openai-whisper, a custom command, or any callable backend via settings.\n"
            "Question: Is the data private?\n"
            "Answer: Yes. Files are stored in your Vault and transcription runs entirely on your server."
        ),
    },
]


class Command(BaseCommand):
    help = "Seed the Transcription app with demo collections, sources, and transcripts."

    def add_arguments(self, parser):
        parser.add_argument("--user", default=None, help="Username of the owner (defaults to first superuser).")
        parser.add_argument("--reset", action="store_true", help="Delete existing demo data before seeding.")

    def handle(self, *args, **options):
        from django.conf import settings as django_settings

        from toto.transcription.models import (
            TranscriptAccessMode,
            TranscriptCollection,
            TranscriptSource,
        )
        from toto.transcription.services import run_transcription, create_transcription_job
        from toto.vault.models import Bucket, VaultFile

        username = options["username"] if "username" in options else options.get("user")
        owner = self._get_owner(username)

        if options["reset"]:
            # Delete the VaultFiles from the demo bucket first (TranscriptSource.source_file
            # uses on_delete=PROTECT, so the files outlive the collection cascade delete).
            from toto.vault.models import Bucket, VaultFile
            demo_bucket = Bucket.objects.filter(slug="transcription-demo").first()
            if demo_bucket:
                vf_count, _ = VaultFile.objects.filter(bucket=demo_bucket).delete()
                demo_bucket.delete()
                self.stdout.write(self.style.WARNING(f"Deleted demo bucket + {vf_count} vault files."))
            col_count, _ = TranscriptCollection.objects.filter(slug=_COLLECTION_SLUG).delete()
            self.stdout.write(self.style.WARNING(f"Deleted {col_count} existing demo collection(s)."))

        bucket = self._get_or_create_bucket(owner)
        collection = self._get_or_create_collection(owner, bucket)

        for spec in _SOURCES:
            source = self._get_or_create_source(spec, collection, owner, bucket)
            if source.status == TranscriptSource.Status.TRANSCRIBED:
                self.stdout.write(f"  ↳ {spec['slug']} — already transcribed, skipping.")
                continue
            self._transcribe(source, spec["transcript"])

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Visit /transcription/ to explore the demo data.\n"
            f"Collection: {collection.get_absolute_url()}"
        ))

    def _get_owner(self, username):
        if username:
            try:
                return User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"User '{username}' not found.")
        owner = User.objects.filter(is_superuser=True).order_by("pk").first()
        if not owner:
            owner = User.objects.order_by("pk").first()
        if not owner:
            raise CommandError("No users exist. Create a user first with createsuperuser.")
        self.stdout.write(f"Using owner: {owner.username}")
        return owner

    def _get_or_create_bucket(self, owner):
        from toto.vault.models import Bucket
        slug = "transcription-demo"
        bucket, created = Bucket.objects.get_or_create(
            slug=slug,
            defaults={"name": "Transcription Demo", "owner": owner},
        )
        if created:
            self.stdout.write(f"Created bucket: {slug}")
        return bucket

    def _get_or_create_collection(self, owner, bucket):
        from toto.transcription.models import TranscriptAccessMode, TranscriptCollection
        col, created = TranscriptCollection.objects.get_or_create(
            slug=_COLLECTION_SLUG,
            defaults={
                "title": "Demo Transcriptions",
                "description": "Auto-seeded demo collection. Safe to delete.",
                "owner": owner,
                "bucket": bucket,
                "access_mode": TranscriptAccessMode.PUBLIC,
                "allow_downloads": True,
            },
        )
        if created:
            self.stdout.write(f"Created collection: {_COLLECTION_SLUG}")
        else:
            self.stdout.write(f"Collection already exists: {_COLLECTION_SLUG}")
        return col

    def _get_or_create_source(self, spec, collection, owner, bucket):
        from toto.transcription.models import TranscriptSource
        from toto.vault.models import VaultFile

        existing = TranscriptSource.objects.filter(collection=collection, slug=spec["slug"]).first()
        if existing:
            self.stdout.write(f"  Source exists: {spec['slug']}")
            return existing

        # Create a tiny synthetic audio file (silence placeholder) and matching .txt sidecar.
        # The sidecar_txt_backend reads <path>.txt for transcription in dev/testing.
        dummy_audio = ContentFile(b"\x00" * 1024, name=f"{spec['slug']}.mp3")
        vf = VaultFile.objects.create(
            owner=owner,
            title=spec["title"],
            bucket=bucket,
            file=dummy_audio,
            file_type="audio",
            is_encrypted=False,
        )
        source = TranscriptSource.objects.create(
            collection=collection,
            source_file=vf,
            title=spec["title"],
            slug=spec["slug"],
            language=spec.get("language", "en"),
            description=f"Demo source: {spec['title']}",
        )
        self.stdout.write(f"  Created source: {spec['slug']}")
        return source

    def _transcribe(self, source, transcript_text: str):
        """Transcribe using sidecar_txt_backend: write a .txt file beside the audio."""
        from toto.transcription.models import TranscriptionJob
        from toto.transcription.services import create_transcription_job, run_transcription

        audio_path = None
        try:
            file_obj = source.source_file.file
            try:
                audio_path = file_obj.path
            except Exception:
                audio_path = None
        except Exception:
            audio_path = None

        if audio_path:
            sidecar = Path(audio_path).with_suffix(Path(audio_path).suffix + ".txt")
            sidecar.write_text(transcript_text, encoding="utf-8")
            prev_backend = self._patch_sidecar_backend()
            try:
                job = create_transcription_job(source=source, engine=TranscriptionJob.Engine.CUSTOM)
                run_transcription(job)
                self.stdout.write(f"  ✓ Transcribed: {source.slug} ({source.segments_count} segments)")
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"  ⚠ Transcription failed for {source.slug}: {exc}"))
            finally:
                self._restore_backend(prev_backend)
                try:
                    sidecar.unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            # Storage backend doesn't expose a path (e.g. S3). Fall back to storing transcript text directly.
            from toto.transcription.models import TranscriptSegment
            job = TranscriptionJob.objects.create(
                source=source,
                status=TranscriptionJob.Status.SUCCESS,
                engine=TranscriptionJob.Engine.CUSTOM,
            )
            for idx, line in enumerate(transcript_text.strip().splitlines(), start=1):
                line = line.strip()
                if not line:
                    continue
                TranscriptSegment.objects.create(
                    job=job, source=source, index=idx,
                    start_ms=(idx - 1) * 3000, end_ms=idx * 3000, text=line,
                )
            from django.utils import timezone
            source.status = source.Status.TRANSCRIBED
            source.transcript_text = transcript_text
            source.segments_count = TranscriptSegment.objects.filter(source=source).count()
            source.transcribed_at = timezone.now()
            source.save(update_fields=["status", "transcript_text", "segments_count", "transcribed_at", "updated_at"])
            self.stdout.write(f"  ✓ Stored text fallback: {source.slug} ({source.segments_count} segments)")

    def _patch_sidecar_backend(self):
        from django.conf import settings as s
        prev = getattr(s, "TRANSCRIPTION_DEFAULT_ENGINE", None)
        s.TRANSCRIPTION_DEFAULT_ENGINE = "custom"
        s.TRANSCRIPTION_BACKEND = "toto.transcription.backends.sidecar_txt_backend"
        return prev

    def _restore_backend(self, prev):
        from django.conf import settings as s
        if prev is None:
            if hasattr(s, "TRANSCRIPTION_DEFAULT_ENGINE"):
                del s.TRANSCRIPTION_DEFAULT_ENGINE
        else:
            s.TRANSCRIPTION_DEFAULT_ENGINE = prev
        if hasattr(s, "TRANSCRIPTION_BACKEND"):
            del s.TRANSCRIPTION_BACKEND
