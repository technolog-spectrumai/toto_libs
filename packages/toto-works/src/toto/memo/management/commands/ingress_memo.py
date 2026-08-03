"""
Seed a demo presentation as a self-contained XML vault file.

Mirrors the file-based model of the memo app: the presentation lives entirely in
the vault (``file_type="presentation"``), with a raster image embedded as a
base64 ``data:`` URI (thumbnailed to ≤640px, as the browser editor does) and an
SVG inlined verbatim — so the file has no external dependencies.
"""

import base64
import io
import os

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.utils.text import slugify

from toto.ingress import IngressCommand
from toto.memo import presentation_format as pf
from toto.vault.models import Bucket, VaultDirectory, VaultFile

MAX_DIM = 640


class Command(IngressCommand):
    help = "Seed a demo self-contained presentation (blocks, layouts, image, svg)."

    def process(self):
        # Demo content only — like the other *full* ingress fixtures.
        if not self.full:
            return

        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        try:
            user = User.objects.get(username=admin_username)
        except User.DoesNotExist:
            self.stdout.write(self.style.WARNING(
                f"User '{admin_username}' not found — skipping memo ingress."
            ))
            return

        bucket, _ = Bucket.objects.get_or_create(
            slug="general",
            defaults={"name": "General", "owner": user},
        )
        directory, _ = VaultDirectory.objects.get_or_create(
            name="Presentations", bucket=bucket, parent=None,
            defaults={"owner": user},
        )

        self._seed_presentation(user, bucket, directory)

    # ──────────────────────────────────────────────────────────────────────────

    def _seed_presentation(self, user, bucket, directory):
        title = "Sample Presentation"
        key = slugify(title)
        if VaultFile.objects.filter(bucket=bucket, key=key).exists():
            self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing presentation: {title}"))
            return

        # A v2 deck, so the demo shows off the layouts rather than four
        # identical stacks of hand-written HTML — which is what the old seed was,
        # and the reason its last slide had to fake two columns with an inline
        # flexbox.
        presentation = pf.Presentation(
            title=title,
            theme="black",
            slides=[
                pf.Slide(
                    title="Welcome to Toto Presentations",
                    layout="title-content",
                    blocks=[
                        pf.Block(type="text", payload=(
                            "<p>Self-contained slideshows, stored as a single "
                            "XML file in your vault.</p>")),
                        pf.Block(type="list", items=[
                            "Blocks you arrange by dragging",
                            "Images embedded as base64 (≤640px)",
                            "SVGs inlined verbatim",
                        ]),
                    ],
                ),
                pf.Slide(
                    title="Embedded image",
                    layout="title-content",
                    blocks=[
                        pf.Block(type="text", payload=(
                            "<p>This PNG lives inside the file — no external "
                            "reference, nothing to lose.</p>")),
                        pf.Block(type="image", attrs={"alt": "Embedded gradient"},
                                 payload=self._demo_png_data_uri()),
                    ],
                ),
                pf.Slide(
                    title="Inline SVG",
                    layout="title-content",
                    blocks=[
                        pf.Block(type="text", payload=(
                            "<p>Vector graphics are pasted straight into the "
                            "XML:</p>")),
                        pf.Block(type="svg", attrs={"alt": "Pipeline"},
                                 payload=self._demo_svg()),
                    ],
                ),
                pf.Slide(
                    title="Two columns",
                    layout="two-column",
                    blocks=[
                        pf.Block(type="text", slot="left", payload=(
                            "<p>The layout does the arranging. Drag a block from "
                            "one column to the other and it stays aligned.</p>")),
                        pf.Block(type="list", slot="left", items=[
                            "No inline styles",
                            "No guessing at widths",
                        ]),
                        pf.Block(type="image", slot="right",
                                 attrs={"alt": "Thumbnail", "fit": "contain"},
                                 payload=self._demo_png_data_uri(size=(320, 200))),
                    ],
                ),
                pf.Slide(
                    title="Part two",
                    layout="section",
                    blocks=[
                        pf.Block(type="heading", attrs={"level": "1"},
                                 payload="Part two"),
                    ],
                ),
                pf.Slide(
                    title="A quote",
                    layout="quote",
                    blocks=[
                        pf.Block(type="quote", attrs={"cite": "Ada Lovelace"},
                                 payload=("The Analytical Engine weaves algebraic "
                                          "patterns, just as the Jacquard loom "
                                          "weaves flowers and leaves.")),
                    ],
                ),
            ],
        )

        xml = pf.dumps(presentation)
        filename = f"{key}.xml"
        vault_file = VaultFile(
            owner=user,
            title=title,
            key=key,
            file_type="xml",
            bucket=bucket,
            directory=directory,
            is_public=True,
            notes="Demo presentation: embedded image + inline SVG.",
        )
        vault_file.file.save(filename, ContentFile(xml.encode("utf-8")), save=False)
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save()

        self.stdout.write(self.style.SUCCESS(
            f"🎞️  Created presentation '{title}' ({len(presentation.slides)} slides, "
            f"{len(xml.encode('utf-8')) // 1024} KB)."
        ))

    # ── Asset builders ─────────────────────────────────────────────────────────

    def _demo_png_data_uri(self, size=(1200, 800)) -> str:
        """A base64 PNG data URI, thumbnailed to ≤640px like the browser editor."""
        try:
            from PIL import Image, ImageDraw
        except Exception:
            # Pillow missing — fall back to a 1×1 transparent PNG so the demo
            # still produces a valid self-contained file.
            tiny = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
            )
            return "data:image/png;base64," + base64.b64encode(tiny).decode("ascii")

        w, h = size
        img = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(img)
        # Diagonal gradient background.
        for y in range(h):
            for x in range(0, w, 8):
                r = int(40 + 180 * (x / w))
                g = int(60 + 150 * (y / h))
                b = int(200 - 120 * (x / w))
                draw.rectangle([x, y, x + 8, y + 1], fill=(r, g, b))
        # A couple of shapes on top.
        draw.ellipse([w * 0.55, h * 0.2, w * 0.9, h * 0.75], fill=(255, 255, 255))
        draw.rectangle([w * 0.1, h * 0.55, w * 0.4, h * 0.8], fill=(20, 30, 60))

        # Thumbnail to the ≤640px cap (matches the editor's client-side resize).
        img.thumbnail((MAX_DIM, MAX_DIM))

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return "data:image/png;base64," + b64

    def _demo_svg(self, width=460) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 160" '
            f'width="{width}" role="img" aria-label="Flow diagram">\n'
            '  <rect x="20" y="45" width="120" height="70" rx="8" '
            'fill="#dbeafe" stroke="#2563eb" stroke-width="3"/>\n'
            '  <rect x="190" y="45" width="120" height="70" rx="8" '
            'fill="#dcfce7" stroke="#16a34a" stroke-width="3"/>\n'
            '  <rect x="360" y="45" width="120" height="70" rx="8" '
            'fill="#fee2e2" stroke="#dc2626" stroke-width="3"/>\n'
            '  <path d="M140 80 H190 M310 80 H360" stroke="#111827" '
            'stroke-width="4" fill="none"/>\n'
            '  <text x="80" y="87" text-anchor="middle" font-size="20">Draft</text>\n'
            '  <text x="250" y="87" text-anchor="middle" font-size="20">Embed</text>\n'
            '  <text x="420" y="87" text-anchor="middle" font-size="20">Present</text>\n'
            "</svg>"
        )
