from __future__ import annotations

import os

from django.core.files import File

from toto.ingress import IngressCommand

from toto.vod.models import VodAccessMode, VodCollection, VodVideo, VodVideoAccessMode
from toto.vod.services import create_video_from_upload, get_or_create_vod_bucket


COLLECTION_SLUG = "incident-footage"
COLLECTION_TITLE = "Incident Footage"
COLLECTION_DESCRIPTION = "Robot incident recordings for review and analysis."

VIDEO_SLUG = "incident"
VIDEO_TITLE = "Incident Recording"
VIDEO_DESCRIPTION = "Recorded incident video imported from data/video/incident.mp4."


class Command(IngressCommand):
    help = "Seed the VOD app with the incident.mp4 demo video."

    def process(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()

        owner = User.objects.filter(is_superuser=True).order_by("id").first()
        if owner is None:
            self.stderr.write(self.style.ERROR("No superuser found — create one first."))
            return

        path = os.path.join(self.DATA_ROOT, "video", "incident.mp4")
        if not os.path.exists(path):
            self.stderr.write(self.style.ERROR(f"Video file not found: {path}"))
            return

        bucket = get_or_create_vod_bucket(owner=owner, name="Incident VOD")

        collection, created = VodCollection.objects.get_or_create(
            slug=COLLECTION_SLUG,
            defaults={
                "title": COLLECTION_TITLE,
                "owner": owner,
                "bucket": bucket,
                "description": COLLECTION_DESCRIPTION,
                "access_mode": VodAccessMode.PUBLIC,
            },
        )
        if created:
            self.stdout.write(f"  + collection '{collection.title}'")
        else:
            self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing collection '{collection.title}'"))

        if VodVideo.objects.filter(collection=collection, slug=VIDEO_SLUG).exists():
            self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing video '{VIDEO_SLUG}'"))
            self.stdout.write(self.style.SUCCESS("✅  VOD ingress complete (already seeded)."))
            return

        self.stdout.write(f"  + importing {path} …")
        with open(path, "rb") as fh:
            video = create_video_from_upload(
                collection=collection,
                uploaded_file=File(fh, name="incident.mp4"),
                owner=owner,
                title=VIDEO_TITLE,
                description=VIDEO_DESCRIPTION,
                status=VodVideo.Status.PUBLISHED,
                access_mode=VodVideoAccessMode.INHERIT,
                build_hls=False,
            )

        self.stdout.write(f"  + video '{video.title}' (pk={video.pk}) created")
        self.stdout.write(self.style.SUCCESS("✅  VOD ingress complete."))
