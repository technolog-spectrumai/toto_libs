// markdown-it-footnote ships no types of its own.
declare module "markdown-it-footnote" {
  import type { PluginSimple } from "markdown-it";
  const footnote: PluginSimple;
  export default footnote;
}
