from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Make the forum channel of every community (idempotent)"

    def process(self):
        """Not demonstration data: a channel with its key and its bucket for
        every community there is, in the realistic and the full mode alike
        (the mode ``none`` seeds nothing, here as everywhere). A community made
        later gets its channel on the first opening of its page. Without
        the forum's secret nothing is made, and the command says so."""
        from toto.forum import channels, keys
        from toto.forum.models import ForumChannel
        from toto.socialhub.models import Community

        made = 0
        for community in Community.objects.order_by("pk"):
            had = ForumChannel.objects.filter(community=community).exists()
            try:
                channels.ensure_channel(community)
            except keys.ChannelKeyUnavailable as exc:
                self.stdout.write(self.style.WARNING(
                    f"Forum channels not made: {exc}"))
                return
            made += 0 if had else 1
        self.stdout.write(self.style.SUCCESS(
            f"Forum channels present: {ForumChannel.objects.count()} ({made} made now)"))
        self._cleanup_workflow()

    def _cleanup_workflow(self):
        """The "Forum cleanup" workflow, so the Workflows tab shows it from
        the first deploy and not from the first cleanup. Nothing on a build
        with no workflow engine."""
        from django.apps import apps

        if not apps.is_installed("toto.workflows"):
            return
        from toto.forum.workflow import ensure_cleanup_workflow

        ensure_cleanup_workflow()
        self.stdout.write("Forum cleanup workflow present")
