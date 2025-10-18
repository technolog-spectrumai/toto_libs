from django.utils.text import slugify
from django.contrib.auth.models import User
from gitops.models import GitRepository, Artifact
from vault.models import Bucket
from oya.ingress import IngressCommand

class Command(IngressCommand):
    help = "Populate the database with a test Git repository and one artifact"

    def process(self, _):
        self.create_dashboard_item(
            title="Git Repositories",
            icon="git-branch",
            description="Creates a test Git repository with a single artifact.",
            link="/repo/"
        )

        try:
            user = User.objects.get(username="admin")
            self.stdout.write(f"Found demo user: {user.username}")
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR("Demo user 'admin' not found. Please create the user first."))
            return

        # Create or get demo bucket
        bucket, _ = Bucket.objects.get_or_create(
            name="Repo Artifacts Bucket",
            owner=user
        )

        # Create repository
        repo, _ = GitRepository.objects.get_or_create(
            name="Test Website",
            defaults={
                "url": "https://github.com/technolog-spectrumai/homebase_website",
                "access_key": "ghp_11BT5SXYA0mogotnAV9mHs_M0YesZLS303cX0R1arJezECsA6Sbloz2W0yk9NbfYpqVEC5EC5SEVtjY3HP",
                "provider": "GitHub"
            }
        )

        # Create single artifact
        Artifact.objects.get_or_create(
            repository=repo,
            branch="main",
            file_path="index.html",
            defaults={"bucket": bucket}
        )

        self.stdout.write(self.style.SUCCESS("Test repository and artifact seeded successfully."))
