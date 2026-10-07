from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Make the forum channel of every community (idempotent)"

    def process(self):
        """Not demonstration data: a channel with its key and its bucket for
        every community there is, in every ingress mode. A community made
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
