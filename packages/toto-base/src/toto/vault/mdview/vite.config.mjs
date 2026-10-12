// Bundles the viewer into one classic script, static/vault/mdview/mdview.js
// (window.TotoMarkdown). Run by build.sh with Morion's checkout, whose
// node_modules hold the bundler and the two packages the renderer needs.
import { resolve } from "node:path";

const here = import.meta.dirname;
const modules = resolve(process.env.MORION || "/home/janek/Desktop/dev/morion", "node_modules");

export default {
  root: here,
  resolve: {
    alias: {
      "markdown-it-footnote": resolve(modules, "markdown-it-footnote"),
      "markdown-it": resolve(modules, "markdown-it"),
    },
  },
  build: {
    outDir: resolve(here, "../static/vault/mdview"),
    emptyOutDir: false,
    minify: true,
    lib: { entry: resolve(here, "main.ts"), name: "TotoMarkdown", formats: ["iife"], fileName: () => "mdview.js" },
  },
};
