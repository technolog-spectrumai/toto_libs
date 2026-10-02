from toto.ingress import IngressCommand
from toto.forum.models import ForumMember, ForumChannel
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils.text import slugify
from toto.people.models import Person
from toto.api.cors import DATA_MESH_GROUP

User = get_user_model()


class Command(IngressCommand):
    help = "Seed sample Forum channels and member rosters"

    def process(self):
        # Before the --full check: the "Forum cleanup" workflow is not demo
        # data. Seeding it here means the Workflows tab shows it from the first
        # deploy rather than from the first night's cleanup.
        # `ensure_cleanup_workflow` is the same get-or-create the dispatcher
        # makes, so this only saves a lookup later.
        from django.apps import apps

        if apps.is_installed("toto.workflows"):
            from toto.forum.workflow import ensure_cleanup_workflow

            workflow = ensure_cleanup_workflow()
            self.stdout.write(self.style.SUCCESS(
                f"Forum cleanup workflow present: {workflow.slug}"))

        if not self.full:
            return

        try:
            admin_user = User.objects.get(username="admin")
        except User.DoesNotExist:
            raise Exception("❌ User 'admin' not found. Please create it first.")

        channel_names = [
            "CandyLand",
            "Announcements"
        ]

        admin_person, _ = Person.objects.get_or_create(
            user=admin_user,
            defaults={
                "display_name": admin_user.get_full_name() or admin_user.username,
                "email": admin_user.email,
            },
        )

        # Grant the admin direct (server-side) read access to the gated data mesh so we
        # can do user testing without pulling data from a peer. We intentionally do NOT
        # add the test/'tester' user to this group for now.
        mesh_group, _ = Group.objects.get_or_create(name=DATA_MESH_GROUP)
        admin_user.groups.add(mesh_group)
        self.stdout.write(
            self.style.SUCCESS(
                f"🔗 Added 'admin' to the '{DATA_MESH_GROUP}' group (data-mesh read access)."
            )
        )

        for name in channel_names:
            channel, created = ForumChannel.objects.get_or_create(
                name=name,
                defaults={
                    "slug": slugify(name),
                    "created_by": admin_user,
                }
            )

            # ForumMember is the only membership record — the parallel participants
            # M2M this used to also write was removed. See permissions.py.
            ForumMember.objects.get_or_create(
                channel=channel,
                person=admin_person,
                defaults={"is_active": True},
            )

            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"💬 Created channel: {channel.name} with admin on the member roster"
                    )
                )
            else:
                self.stdout.write(self.style.WARNING(f"⚠️ Channel already exists: {channel.name}"))

        self.stdout.write(self.style.SUCCESS("✅ Forum channel seeding complete."))
