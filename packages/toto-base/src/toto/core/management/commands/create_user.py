import os

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
            elif not user.email:
                # NEVER LEAVE AN ACCOUNT WITHOUT ONE. Gitea refuses to
                # auto-register an OIDC identity whose `email` claim is empty
                # — it logs "provider doesn't return required fields: email"
                # and drops the user on /user/link_account, a form with no
                # local password to link with because password sign-in is off.
                # ADMIN_EMAIL defaults to "" in init_data, so the one account
                # every deployment has could never sign in to the forge.
                #
                # Derived, not invented as a mailbox: a stable unique address
                # that satisfies the services requiring the claim. Set
                # ADMIN_EMAIL for a real one. `.invalid` is RFC 2606-reserved
                # so a placeholder cannot collide with somebody's real address.
                domain = (os.environ.get("PLATFORM_DOMAIN", "") or "").strip()
                domain = domain.split("//")[-1].split("/")[0].strip()
                if not domain or domain in ("localhost", "127.0.0.1"):
                    domain = "localhost.invalid"
                user.email = f"{username}@{domain}"
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
