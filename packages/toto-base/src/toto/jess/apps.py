from django.apps import AppConfig


class JessConfig(AppConfig):
    """The platform's post — see README.md for the cat this is named after.

    Deliberately NO ready() hook. Two things it might have been tempted to do are
    both wrong here:

    * asserting that toto.gervazy is installed — gervazy is a CORE_APP
      (registry.py:13), part of the irreducible core/gervazy/people/locations/events
      cycle, so a host cannot have jess without it;
    * touching the database or opening the vault — ready() runs before migrations
      and during collectstatic, so anything that queries fails on a fresh tree.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.jess"
    label = "jess"
    verbose_name = "Jess (mail)"
