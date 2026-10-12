// Bundles the sheet viewer into one classic script and one stylesheet,
// static/vault/deckview/deckview.{js,css} (window.TotoDeck). build.sh
// copies the sources into Morion's checkout for the build, so reveal.js and
// what it needs resolve from Morion's node_modules by Node's own rules;
// OUT names where the bundle goes.
import { resolve } from "node:path";

const here = import.meta.dirname;

export default {
  root: here,
  define: { "process.env.NODE_ENV": '"production"' },
  build: {
    outDir: process.env.OUT,
    emptyOutDir: false,
    minify: true,
    cssCodeSplit: false,
    lib: { entry: resolve(here, "main.ts"), name: "TotoDeck", formats: ["iife"], fileName: () => "deckview.js",
           cssFileName: "deckview" },
  },
};
