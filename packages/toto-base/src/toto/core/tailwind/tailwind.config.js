// The one Tailwind configuration of every toto host (2026-10-10).
//
// Each host carried its own copy of this file under deploy/tailwind/, and
// its colours read CSS variables (--oya-<token>: R G B) that oya/base.html
// wrote on every page from a database row, the platform's theme. There is
// one palette now, ../palette.json (toto/core/palette.py), and it is read
// HERE, when the image is built: the hex values are compiled into
// oya/tailwind.css. Tailwind gives a hex colour its opacity modifiers
// itself, so bg-warn-light/5 works as it did.
//
// * the theme font is still --oya-font-sans, which oya/base.html writes: the
//   font is a database row (Platform.font) with a stylesheet link of its own;
// * darkMode is left at the Play CDN's default, "media": dark: follows the
//   visitor's system, the header's toggle drives the :class bindings;
// * no plugins: the Play CDN script the pages loaded carried none;
// * the safelist is the HOST's: the class names its pages build from pieces.
//   Its file is named by $TOTO_TAILWIND_SAFELIST, which the host's
//   scripts/tailwind_build.py sets; a build without one safelists nothing.
//
// `content` is given by the host's scripts/tailwind_build.py (--content),
// which resolves the installed packages and the host's sources; it is empty
// here so that nothing is scanned that the script did not choose.

const palette = require("../palette.json");

const safelistFile = process.env.TOTO_TAILWIND_SAFELIST;
const safelist = safelistFile
  ? require(safelistFile).sites.flatMap((site) => site.classes)
  : [];

module.exports = {
  content: [],
  safelist,
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--oya-font-sans)"],
      },
      colors: palette.colors,
    },
  },
  plugins: [],
};
