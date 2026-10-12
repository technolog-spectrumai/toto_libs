// From morion/apps/morion/src/files/openWith.ts: the one function uson.ts needs.
export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot + 1).toLowerCase();
}
