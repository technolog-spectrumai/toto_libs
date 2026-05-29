from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.utils.text import slugify

from toto.ingress import IngressCommand
from toto.vault.models import Bucket, VaultFile
from toto.vod.models import VodAccessMode, VodCollection, VodVideo
from toto.vod.services import get_or_create_vod_bucket

User = get_user_model()

# ---------------------------------------------------------------------------
# Dummy collection definitions
# ---------------------------------------------------------------------------

_COLLECTIONS = [
    {
        "slug": "incident-footage",
        "title": "Incident Footage",
        "description": "Robot incident recordings for review and analysis.",
        "access_mode": VodAccessMode.PUBLIC,
        "videos": [
            {
                "slug": "incident",
                "title": "Incident Recording",
                "description": "Recorded incident video imported from data/video/incident.mp4.",
                "use_real_file": True,   # try to import real file; fall back to placeholder
            },
        ],
    },
    {
        "slug": "tutorial-videos",
        "title": "Tutorial Videos",
        "description": "Step-by-step tutorials for platform features.",
        "access_mode": VodAccessMode.PUBLIC,
        "videos": [
            {"slug": "getting-started",      "title": "Getting Started",         "description": "Platform overview and first steps."},
            {"slug": "vault-walkthrough",     "title": "Vault Walkthrough",       "description": "How to upload, organise, and share files."},
            {"slug": "kanban-basics",         "title": "Kanban Basics",           "description": "Creating projects, sprints, and tasks."},
        ],
    },
    {
        "slug": "presentations",
        "title": "Presentations",
        "description": "Community and team presentations.",
        "access_mode": VodAccessMode.PUBLIC,
        "videos": [
            {"slug": "q1-community-update",  "title": "Q1 Community Update",     "description": "Quarterly update for Our Thing Inc. members."},
            {"slug": "roadmap-2026",         "title": "2026 Roadmap",            "description": "Platform roadmap and upcoming features."},
            {"slug": "annual-report-2025",   "title": "Annual Report 2025",      "description": "Highlights and financials from last year."},
        ],
    },
    {
        "slug": "training-material",
        "title": "Training Material",
        "description": "Internal training content — members only.",
        "access_mode": VodAccessMode.PRIVATE,
        "videos": [
            {"slug": "onboarding-101",       "title": "Onboarding 101",          "description": "New-member orientation."},
            {"slug": "security-awareness",   "title": "Security Awareness",      "description": "Platform security and best practices."},
        ],
    },
    {
        "slug": "archive",
        "title": "Archive",
        "description": "Older recordings kept for reference.",
        "access_mode": VodAccessMode.PRIVATE,
        "videos": [
            {"slug": "founding-meeting-2024","title": "Founding Meeting 2024",   "description": "The original founding session recording."},
        ],
    },
]


class Command(IngressCommand):
    help = "Seed the VOD app with demo collections and placeholder videos."

    def process(self):
        owner = User.objects.filter(is_superuser=True).order_by("id").first()
        if owner is None:
            self.stderr.write(self.style.ERROR("No superuser found — create one first."))
            return

        bucket = get_or_create_vod_bucket(owner=owner, name="Demo VOD Library")

        for coll_def in _COLLECTIONS:
            collection, coll_created = VodCollection.objects.get_or_create(
                slug=coll_def["slug"],
                defaults={
                    "title": coll_def["title"],
                    "owner": owner,
                    "bucket": bucket,
                    "description": coll_def["description"],
                    "access_mode": coll_def["access_mode"],
                },
            )
            if coll_created:
                self.stdout.write(self.style.SUCCESS(f"  + collection '{collection.title}'"))
            else:
                self.stdout.write(f"  ~ collection '{collection.title}' (exists)")

            for vid_def in coll_def.get("videos", []):
                self._ensure_video(owner, bucket, collection, vid_def)

        self.stdout.write(self.style.SUCCESS("VOD ingress complete."))

    # ------------------------------------------------------------------

    def _ensure_video(self, owner, bucket, collection, vid_def):
        slug = vid_def["slug"]
        if VodVideo.objects.filter(collection=collection, slug=slug).exists():
            self.stdout.write(f"    ~ video '{slug}' (exists)")
            return

        # Try to import from real file for the incident collection
        if vid_def.get("use_real_file"):
            real_path = os.path.join(self.DATA_ROOT, "video", "incident.mp4")
            if os.path.exists(real_path):
                try:
                    from django.core.files import File
                    from toto.vod.services import create_video_from_upload

                    with open(real_path, "rb") as fh:
                        video = create_video_from_upload(
                            collection=collection,
                            uploaded_file=File(fh, name="incident.mp4"),
                            owner=owner,
                            title=vid_def["title"],
                            description=vid_def["description"],
                            status=VodVideo.Status.PUBLISHED,
                            access_mode=None,
                            build_hls=False,
                        )
                    self.stdout.write(self.style.SUCCESS(f"    + video '{video.title}' (from file)"))
                    return
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f"    ! real file import failed ({exc}), using placeholder"))

        # Create placeholder VaultFile (no actual video bytes)
        placeholder_content = f"[VOD placeholder: {vid_def['title']}]\n".encode()
        vault_file = VaultFile(
            owner=owner,
            bucket=bucket,
            title=vid_def["title"],
            key=slugify(vid_def["slug"]),
            file_type="video",
            is_public=(collection.access_mode == VodAccessMode.PUBLIC),
            notes=f"Placeholder for demo video: {vid_def['description']}",
        )
        vault_file.file.save(
            f"{slugify(vid_def['slug'])}.mp4",
            ContentFile(placeholder_content),
            save=False,
        )
        vault_file.save()

        VodVideo.objects.create(
            collection=collection,
            source_file=vault_file,
            slug=slug,
            title=vid_def["title"],
            description=vid_def["description"],
            status=VodVideo.Status.DRAFT,
        )
        self.stdout.write(self.style.SUCCESS(f"    + video '{vid_def['title']}' (placeholder)"))
