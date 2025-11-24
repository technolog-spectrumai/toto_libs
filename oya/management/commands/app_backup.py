# toto/backup.py

from django.core.management.base import BaseCommand, CommandError
from django.apps import apps
from vault.models import Bucket
from toto.serialize import ModelSerializer
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    """
    Generic backup command that can dump any serializable model
    into a Vault bucket. No need for per-app commands.
    """

    help = "Backup data for a given app/model into a Vault bucket"

    def __init__(self):
        super().__init__()
        self.full = False

    def add_arguments(self, parser):
        parser.add_argument(
            '--full',
            action='store_true',
            help="If set, run a full backup; otherwise, only process indispensable data"
        )
        parser.add_argument(
            '--app',
            type=str,
            required=True,
            help="Target app label (e.g. 'communities')"
        )
        parser.add_argument(
            '--model',
            type=str,
            required=True,
            help="Target model name (e.g. 'Community')"
        )
        parser.add_argument(
            '--bucket',
            type=str,
            required=True,
            help="Target bucket name for backup"
        )
        parser.add_argument(
            '--filename',
            type=str,
            required=True,
            help="Filename to use for backup dump"
        )

    def handle(self, *args, **options):
        self.full = options.get('full', False)
        app_label = options['app']
        model_name = options['model']
        bucket_name = options['bucket']
        filename = options['filename']

        # Resolve model
        try:
            model_class = apps.get_model(app_label, model_name)
        except LookupError:
            raise CommandError(f"Model {app_label}.{model_name} not found")

        # Resolve bucket
        try:
            bucket = Bucket.objects.get(name=bucket_name)
        except Bucket.DoesNotExist:
            raise CommandError(f"Bucket '{bucket_name}' does not exist")

        # Resolve owner (first superuser)
        User = get_user_model()
        owner = User.objects.filter(is_superuser=True).first()
        if not owner:
            raise CommandError("No superuser found to assign as backup owner")

        # Queryset
        queryset = model_class.objects.all()
        if not queryset.exists():
            self.stdout.write(self.style.WARNING(f"⚠️ No {model_name} objects found to backup"))
            return

        # Dump
        serializer = ModelSerializer(model_class)
        serializer.dump_queryset_to_bucket(queryset, owner, bucket, filename)

        self.stdout.write(self.style.SUCCESS(
            f"Dumped {queryset.count()} {model_name} objects to {filename} in bucket {bucket.name}"
        ))
