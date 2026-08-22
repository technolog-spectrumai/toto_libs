from django.apps import AppConfig


class PollsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.polls'

    # No plugin discovery any more. This app used to import an `electorates`
    # and a `governance` registry here and scan every installed app for more:
    # who was entitled to vote on a formal question, and what a scope's rules
    # made of a finished count. Both were governance, both left in 1.50 —
    # formal company governance belongs to Irena, and its presentation to
    # Ireneo. What is left counts responses, and a consultation has no
    # electorate to resolve: everyone who may see the question may answer it.
