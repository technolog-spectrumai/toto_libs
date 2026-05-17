# render_biome_legend.py

from PIL import Image, ImageDraw, ImageFont

# Import your BIOMES dict from wherever it lives
# Change this import to match your project.
import biomes


def get_biome_name(biome):
    return (
        getattr(biome, "display_name", None)
        or getattr(biome, "label", None)
        or getattr(biome, "name", None)
        or str(biome)
    )


def get_biome_color(biome):
    return (
        getattr(biome, "color", None)
        or getattr(biome, "hex_color", None)
        or getattr(biome, "colour", None)
        or "#64748b"
    )


def render_biome_legend(
    output_path="biome_legend.png",
    columns=2,
    swatch_size=30,
    row_height=44,
    padding=28,
    gap=14,
):
    items = [
        (key, get_biome_name(biome), get_biome_color(biome))
        for key, biome in biomes.BIOMES.items()
        if key != "unknown"
    ]

    rows_per_column = (len(items) + columns - 1) // columns

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 18)
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
        title_font = ImageFont.load_default()

    dummy = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(dummy)

    def text_size(text, font_obj):
        box = draw.textbbox((0, 0), text, font=font_obj)
        return box[2] - box[0], box[3] - box[1]

    max_text_width = max(text_size(name, font)[0] for _, name, _ in items)
    title = "Biome Legend"
    title_width = text_size(title, title_font)[0]

    column_width = swatch_size + gap + max_text_width + padding
    width = max(title_width + padding * 2, padding * 2 + column_width * columns)
    height = padding * 2 + 48 + rows_per_column * row_height

    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)

    draw.text((padding, padding), title, fill="#111827", font=title_font)

    start_y = padding + 48

    for index, (_, name, color) in enumerate(items):
        column = index // rows_per_column
        row = index % rows_per_column

        x = padding + column * column_width
        y = start_y + row * row_height

        draw.rounded_rectangle(
            (x, y, x + swatch_size, y + swatch_size),
            radius=6,
            fill=color,
            outline="#1f2937",
            width=1,
        )

        draw.text(
            (x + swatch_size + gap, y + 4),
            name,
            fill="#111827",
            font=font,
        )

    image.save(output_path)
    print(f"Saved biome legend to: {output_path}")


if __name__ == "__main__":
    render_biome_legend("biome_legend.png", columns=2)