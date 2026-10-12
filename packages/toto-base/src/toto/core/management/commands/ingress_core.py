from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Core's ingress step: nothing to seed since the palette is a file"

    def process(self):
        # This command made the extra demo themes from the host's
        # data/themes/*.json, on a full ingress only. There are no theme rows
        # since 2026-10-10: the one palette is toto/core/palette.json, read
        # when the image is built. The federation ("Toto-Federation" +
        # okti.png) is created by init_data, as before. The command stays
        # because every app has one and ingress_all runs them by name.
        self.stdout.write("Core: the palette is toto/core/palette.json; no themes to seed.")
