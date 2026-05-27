from django.core.management.base import BaseCommand, CommandError

from toto.vod.models import VodVideo
from toto.vod.services import build_hls_for_video


class Command(BaseCommand):
    help = "Build HLS VOD output (.m3u8 + .ts segments) for one or more VodVideo records."

    def add_arguments(self, parser):
        parser.add_argument("video_id", nargs="?", type=int)
        parser.add_argument("--missing", action="store_true", help="Build all published/ready-missing videos.")
        parser.add_argument("--force", action="store_true", help="Delete and rebuild existing HLS output.")
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--segment-seconds", type=int, default=None)

    def handle(self, *args, **options):
        video_id = options.get("video_id")
        if not video_id and not options["missing"]:
            raise CommandError("Pass a video_id or --missing.")

        if video_id:
            videos = VodVideo.objects.filter(id=video_id).select_related("collection", "source_file")
        else:
            videos = VodVideo.objects.filter(hls_ready=False).select_related("collection", "source_file")[: options["limit"]]

        count = 0
        for video in videos:
            self.stdout.write(f"Building HLS for #{video.id}: {video.title}")
            try:
                build_hls_for_video(video, force=options["force"], segment_seconds=options.get("segment_seconds"))
            except Exception as exc:
                raise CommandError(str(exc)) from exc
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Built HLS for {count} video(s)."))
