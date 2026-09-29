"""Decks a new clearance may keep (2026-09-30): the vault's file kind, for the
deck types this app shows (socialhub's ``ClearanceTargetPlugin``)."""

from django.utils.translation import gettext_lazy as _

from toto.socialhub.plugins.clearance_plugins import ClearanceTargetPlugin
from toto.vault.plugins.clearance_plugins import VaultFileKind


@ClearanceTargetPlugin.plugin(key="memo.deck", title=_("Presentations"), order=22)
class Decks(VaultFileKind):
    icon = "person-chalkboard"
    vault_file_types = ("pxml", "presentation")      # memo.views.DECK_TYPES
