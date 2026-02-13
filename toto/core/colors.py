import random
import matplotlib.pyplot as plt


class ColorGenerator:
    """
    Utility class for generating colors from Matplotlib palettes.
    """

    def __init__(self, palette_name="tab20"):
        self.palette_name = palette_name
        self.colors = self._load_palette(palette_name)
        random.shuffle(self.colors)

    @staticmethod
    def _rgb_to_hex(rgb):
        """Convert (r, g, b) floats to #RRGGBB hex."""
        return "#{:02x}{:02x}{:02x}".format(
            int(rgb[0] * 255),
            int(rgb[1] * 255),
            int(rgb[2] * 255),
        )

    def _load_palette(self, name):
        """Load a Matplotlib palette and convert to hex colors."""
        cmap = plt.get_cmap(name)
        return [self._rgb_to_hex(c) for c in cmap.colors]

    def random_color(self):
        """Return a random color from the palette."""
        return random.choice(self.colors)

    def set_palette(self, name):
        """Switch to a different Matplotlib palette."""
        self.palette_name = name
        self.colors = self._load_palette(name)

    @staticmethod
    def random_mpl_color(palette_name="tab20"):
        generator = ColorGenerator(palette_name)
        return generator.random_color()

    def color_for_id(self, id_value):
        return self.colors[id_value % len(self.colors)]
