from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

User = get_user_model()

class Command(BaseCommand):
    help = 'Create or update a superuser with the given username, email, and password'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='The username for the superuser')
        parser.add_argument('password', type=str, help='The password for the superuser')
        parser.add_argument('--admin', type=bool, default=False, help='is admin')

    def handle(self, *args, **options):
        username = options['username']
        password = options['password']

        try:
            user, created = User.objects.update_or_create(
                username=username,
            )
            user.set_password(password)
            if options.get("admin"):
                user.is_superuser = True
                user.is_staff = True
            user.save()

            if created:
                self.stdout.write(self.style.SUCCESS(f'Successfully created superuser: {username}'))
            else:
                self.stdout.write(self.style.SUCCESS(f'Successfully updated superuser: {username}'))
        except Exception as e:
            raise CommandError(f'Error: {e}')
