from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

User = get_user_model()

class Command(BaseCommand):
    help = 'Create or update a superuser with the given username, email, and password'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='The username for the superuser')
        parser.add_argument('password', type=str, help='The password for the superuser')
        parser.add_argument('--admin', type=bool, default=False, help='is admin')
        parser.add_argument('--email', type=str, default='', help='Email address')
        parser.add_argument('--first-name', type=str, default='', help='First name')
        parser.add_argument('--last-name', type=str, default='', help='Last name')

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
            if options.get("email"):
                user.email = options["email"]
            if options.get("first_name"):
                user.first_name = options["first_name"]
            if options.get("last_name"):
                user.last_name = options["last_name"]
            user.save()

            if created:
                self.stdout.write(self.style.SUCCESS(f'Successfully created superuser: {username}'))
            else:
                self.stdout.write(self.style.SUCCESS(f'Successfully updated superuser: {username}'))
        except Exception as e:
            raise CommandError(f'Error: {e}')
