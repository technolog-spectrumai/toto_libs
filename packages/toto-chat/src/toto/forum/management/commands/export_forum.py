"""Build the archive off-request.

The escape hatch for a forum too big for a browser to sit through, and the
thing to reach for when `--no-caps` is genuinely warranted. Writes exactly what
the web export writes, through the same generator, so there is one archive
format and not two.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Write the whole forum to a ZIP that opens as an offline website."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default="",
                            help="Where to write it. Defaults to the "
                                 "generated filename in the current folder.")
        parser.add_argument(
            "--no-caps", action="store_true",
            help="Lift the total-messages and total-size limits. The per-room "
                 "limit stays: it bounds what a browser can open, not what "
                 "this process can do.")

    def handle(self, *args, **options):
        from toto.forum import export

        if options["no_caps"]:
            export.lift_caps()
            self.stdout.write("caps lifted for totals; per-room limit stands")

        try:
            plan = export.survey(actor="management command")
        except export.ExportTooLarge as exc:
            raise CommandError(str(exc)) from exc

        target = Path(options["path"] or export.export_filename())
        written = 0
        with target.open("wb") as fh:
            for chunk in export.stream_archive(plan):
                fh.write(chunk)
                written += len(chunk)

        self.stdout.write(
            f"{target}: {plan.total_messages} messages, "
            f"{len(plan.rooms)} rooms, {len(plan.attachments)} files, "
            f"{written} bytes")
        if plan.skipped:
            self.stdout.write(f"{len(plan.skipped)} file(s) skipped — see "
                              f"manifest.json inside the archive")
