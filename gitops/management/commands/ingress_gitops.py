from django.utils.text import slugify
from django.contrib.auth.models import User
from repo.models import GitRepository, Artifact
from oya.ingress import IngressCommand
import random

class Command(IngressCommand):
    help = "Populate the database with sample Git repositories and artifacts"

    def process(self, _):
        self.create_dashboard_item(
            title="Git Repositories",
            icon="git-branch",
            description="Creates sample Git repositories with artifacts for demo/testing.",
            link="/repo/"
        )

        # Sample repositories
        repos = [
            {"name": "Sample Repo A", "url": "https://github.com/example/repo-a"},
            {"name": "Sample Repo B", "url": "https://gitlab.com/example/repo-b"},
            {"name": "Sample Repo C", "url": "https://bitbucket.org/example/repo-c"},
        ]

        for repo_data in repos:
            repo, _ = GitRepository.objects.get_or_create(
                name=repo_data["name"],
                defaults={
                    "url": repo_data["url"],
                    "access_key": f"key-{slugify(repo_data['name'])}"
                }
            )

            # Sample artifacts
            for i in range(3):
                Artifact.objects.get_or_create(
                    repository=repo,
                    branch=random.choice(["main", "develop", "feature-x"]),
                    file_path=f"src/module_{i}/file_{i}.py"
                )
