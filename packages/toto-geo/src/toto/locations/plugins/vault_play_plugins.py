"""Play on a ``.geojson`` file of the vault (2026-10-12; the owner: "add play
button with geojson which will display it on the map like in locations
tab"): the GeoJSON tab of Locations, with that file chosen and drawn. Only
for somebody who has the map (``links.map_open_to``): a button that opens
the plans page is worse than none."""

from django.urls import reverse

from toto.vault.plugins import VaultPlayPlugin


@VaultPlayPlugin.plugin(key="geojson", file_type="geojson")
class GeoJsonPlayPlugin(VaultPlayPlugin):
    def is_open_to(self, user) -> bool:
        from toto.locations.links import map_open_to

        return map_open_to(user)

    def get_play_url(self, vault_file) -> str:
        return reverse("locations:geojson") + f"?file={vault_file.pk}"
