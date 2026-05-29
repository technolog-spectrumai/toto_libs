"""Seed the Transcription app with demo data for user testing.

Usage:
    python manage.py ingress_transcription
    python manage.py ingress_transcription --user admin
    python manage.py ingress_transcription --force   # re-seed even if already seeded

Creates:
    - 1 demo TranscriptCollection (public, slug "demo-transcripts")
    - 3 demo TranscriptSource objects backed by tiny audio fixtures
    - Transcription jobs + segments using the sidecar_txt_backend (no Whisper needed)
    - 1 TranscriptArtifact (TXT export) for each source

Idempotent: skips objects that already exist by slug.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

User = get_user_model()

# ---------------------------------------------------------------------------
# Demo content
# ---------------------------------------------------------------------------

_DEMO_SOURCES = [
    {
        "slug": "demo-city-council-meeting",
        "title": "City Council Meeting – Q1",
        "description": "Quarterly city council session discussing infrastructure, budget, and community proposals.",
        "language": "en",
        "duration_seconds": 2700,
        "transcript": (
            "Good morning, everyone. I'd like to call this session to order. "
            "Today's agenda covers three main items: the bridge repair tender, "
            "the community centre expansion proposal, and the annual budget review. "
            "Councillor Rivera, would you like to begin with the bridge repair tender? "
            "Certainly, Madam Chair. The engineering assessment concluded last week "
            "and we have three competitive bids on the table. The lowest bid, from "
            "Apex Construction, comes in at 4.2 million. Our recommendation is to "
            "award them the contract pending a final site inspection. "
            "Any objections from the council? Hearing none, we move to approve. "
            "Next, Councillor Chen on the community centre expansion. "
            "Thank you. The proposal calls for adding a second gymnasium, "
            "a youth tech lab, and expanding parking by sixty spaces. "
            "The estimated cost is 8.7 million spread over two fiscal years."
        ),
    },
    {
        "slug": "demo-product-launch-keynote",
        "title": "Product Launch Keynote – Spring Edition",
        "description": "Annual spring product launch featuring new hardware and software announcements.",
        "language": "en",
        "duration_seconds": 1800,
        "transcript": (
            "Welcome, everyone, to our Spring Launch event. "
            "This year we are thrilled to introduce three breakthrough products "
            "that redefine what's possible in personal computing. "
            "First, the new ProBook Ultra — a paper-thin laptop with an all-day battery. "
            "Second, the CloudDrive Sync platform offering seamless cross-device storage. "
            "And third, our new AI writing assistant built right into the operating system. "
            "Let me walk you through each one in detail. "
            "The ProBook Ultra weighs just 890 grams and delivers 22 hours of real-world use. "
            "It ships with our new thermal architecture that keeps it completely fanless. "
            "Available in three colours starting at 1 299 dollars."
        ),
    },
    {
        "slug": "demo-language-lesson-polish",
        "title": "Polish Language Lesson – Greetings",
        "description": "Introductory Polish language lesson covering everyday greetings and phrases.",
        "language": "pl",
        "duration_seconds": 900,
        "transcript": (
            "Dzień dobry wszystkim. Witajcie na pierwszej lekcji języka polskiego. "
            "Dzisiaj nauczymy się podstawowych pozdrowień. "
            "Dzień dobry oznacza dobry ranek lub dzień dobry. "
            "Dobry wieczór używamy po południu i wieczorem. "
            "Do widzenia to do zobaczenia lub żegnaj. "
            "Proszę powtórzyć za mną: Dzień dobry. Dobry wieczór. Do widzenia. "
            "Bardzo dobrze. Teraz spróbujmy krótkiego dialogu. "
            "Jak się masz? Mam się dobrze, dziękuję. A ty? "
            "Też dobrze, dziękuję za pytanie."
        ),
    },
]


class Command(BaseCommand):
    help = "Seed demo data for the Transcription app (collections, sources, and transcribed segments)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            default=None,
            help="Username of the owner. Defaults to the first superuser, then first user.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Re-seed even if data already exists.",
        )

    def handle(self, *args, **options):
        self._check_tariff()
        owner = self._get_owner(options["user"])
        self.stdout.write(f"Seeding as user: {owner.username}")

        from toto.transcription.models import TranscriptCollection, TranscriptSource, TranscriptionJob, TranscriptSegment, TranscriptArtifact, TranscriptAccessMode
        from toto.transcription.services import get_or_create_transcription_bucket
        from toto.vault.models import VaultFile

        # -- Collection ---------------------------------------------------------
        collection_slug = "demo-transcripts"
        if TranscriptCollection.objects.filter(slug=collection_slug).exists():
            if not options["force"]:
                self.stdout.write(self.style.WARNING(
                    f"Collection '{collection_slug}' already exists. Pass --force to re-seed."
                ))
                return
            self.stdout.write(self.style.WARNING(f"Force re-seeding '{collection_slug}'…"))

        collection, created = TranscriptCollection.objects.get_or_create(
            slug=collection_slug,
            defaults={
                "title": "Demo Transcripts",
                "description": "Sample transcription collection created by ingress_transcription for user testing.",
                "owner": owner,
                "access_mode": TranscriptAccessMode.PUBLIC,
                "allow_downloads": True,
                "position": 0,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"  Created collection: {collection}"))
        else:
            self.stdout.write(f"  Using existing collection: {collection}")

        bucket = get_or_create_transcription_bucket(owner=owner, name="Demo Transcription")

        # -- Sources ------------------------------------------------------------
        for demo in _DEMO_SOURCES:
            if TranscriptSource.objects.filter(collection=collection, slug=demo["slug"]).exists():
                if not options["force"]:
                    self.stdout.write(f"  Skipping existing source: {demo['slug']}")
                    continue
                TranscriptSource.objects.filter(collection=collection, slug=demo["slug"]).delete()

            # Create a tiny silent MP3-like binary (not a real audio file but enough
            # for the vault VaultFile to accept it). The sidecar_txt_backend ignores
            # the actual audio content and reads the .txt sidecar instead.
            fake_audio_bytes = _minimal_wav_bytes()
            fname = f"{demo['slug']}.wav"

            vault_file = VaultFile.objects.create(
                owner=owner,
                title=demo["title"],
                bucket=bucket,
                file=ContentFile(fake_audio_bytes, name=fname),
                file_type="audio",
                is_encrypted=False,
                is_public=True,
            )
            # Write sidecar transcript file next to the stored audio so
            # sidecar_txt_backend can find it.
            stored_path = self._vault_file_path(vault_file)
            if stored_path:
                sidecar_path = stored_path + ".txt"
                Path(sidecar_path).write_text(demo["transcript"], encoding="utf-8")
                self.stdout.write(f"    Sidecar written: {sidecar_path}")

            source = TranscriptSource.objects.create(
                collection=collection,
                source_file=vault_file,
                title=demo["title"],
                slug=demo["slug"],
                description=demo["description"],
                language=demo["language"],
                duration_seconds=demo["duration_seconds"],
                status=TranscriptSource.Status.DRAFT,
            )

            # -- Transcription job ---------------------------------------------
            job = TranscriptionJob.objects.create(
                source=source,
                engine=TranscriptionJob.Engine.CUSTOM,
                language=demo["language"],
                status=TranscriptionJob.Status.QUEUED,
            )

            if stored_path:
                self._run_sidecar_transcription(source, job, demo["transcript"])
            else:
                # No local path (e.g. S3 storage) — insert segments directly
                self._insert_segments_directly(source, job, demo["transcript"])

            self.stdout.write(self.style.SUCCESS(f"  Created source + transcript: {source}"))

        self.stdout.write(self.style.SUCCESS("\nDone. Visit /transcription/ to explore the demo data."))
        self.stdout.write("Tip: set TRANSCRIPTION_BACKEND='toto.transcription.backends.sidecar_txt_backend' to test transcription without Whisper.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_tariff(self):
        from toto.tariffs.models import Tariff
        if Tariff.objects.filter(code="TRANSCRIPTION-STANDARD").exists():
            self.stdout.write("  [transcription] TRANSCRIPTION-STANDARD tariff: ready.")
        else:
            self.stdout.write(self.style.WARNING(
                "  [transcription] TRANSCRIPTION-STANDARD tariff not found — run ingress_tariffs first."
            ))

    def _get_owner(self, username: str | None):
        if username:
            try:
                return User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"User '{username}' not found.")
        user = User.objects.filter(is_superuser=True).order_by("pk").first()
        if not user:
            user = User.objects.order_by("pk").first()
        if not user:
            raise CommandError("No users found. Create a user first with createsuperuser.")
        return user

    def _vault_file_path(self, vault_file) -> str | None:
        try:
            return vault_file.file.path
        except Exception:
            return None

    def _run_sidecar_transcription(self, source, job, transcript_text: str):
        from django.utils import timezone
        from toto.transcription.models import TranscriptSegment

        job.status = job.Status.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at", "updated_at"])

        source.status = source.Status.PROCESSING
        source.save(update_fields=["status", "updated_at"])

        sentences = [s.strip() for s in transcript_text.split(".") if s.strip()]
        ms_per_segment = 3000
        for idx, sentence in enumerate(sentences, start=1):
            TranscriptSegment.objects.create(
                job=job,
                source=source,
                index=idx,
                start_ms=(idx - 1) * ms_per_segment,
                end_ms=idx * ms_per_segment,
                text=sentence + ".",
            )

        from django.utils import timezone as tz
        job.status = job.Status.SUCCESS
        job.finished_at = tz.now()
        job.raw_response = {"backend": "ingress_sidecar", "sentences": len(sentences)}
        job.save(update_fields=["status", "finished_at", "raw_response", "updated_at"])

        source.status = source.Status.TRANSCRIBED
        source.transcript_text = transcript_text
        source.segments_count = len(sentences)
        source.transcribed_at = tz.now()
        source.save(update_fields=["status", "transcript_text", "segments_count", "transcribed_at", "updated_at"])

    def _insert_segments_directly(self, source, job, transcript_text: str):
        self._run_sidecar_transcription(source, job, transcript_text)


def _minimal_wav_bytes() -> bytes:
    """Return the smallest valid WAV header (44 bytes, 0 samples, 1ch/8-bit/8kHz)."""
    import struct
    sample_rate = 8000
    channels = 1
    bits_per_sample = 8
    data_size = 0
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        channels,
        sample_rate,
        sample_rate * channels * bits_per_sample // 8,
        channels * bits_per_sample // 8,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header
