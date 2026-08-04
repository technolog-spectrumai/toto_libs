"""Fetch the pinned TipTap/ProseMirror ESM closure into cyprian's vendor dir.

Run once, by hand, when the pin changes. The files it writes are COMMITTED —
this is not a build step, and nothing runs it at deploy time.
"""
import json
import pathlib
import re
import urllib.request

CDN = "https://cdn.jsdelivr.net/npm/"
DEST = (pathlib.Path(__file__).resolve().parent.parent
        / "static" / "cyprian" / "vendor" / "tiptap")

TIPTAP = "2.27.2"

# Entry points we actually import. Everything else arrives by walking imports.
ENTRIES = [
    "@tiptap/core", "@tiptap/starter-kit", "@tiptap/extension-image",
    "@tiptap/extension-table", "@tiptap/extension-table-row",
    "@tiptap/extension-table-cell", "@tiptap/extension-table-header",
    "@tiptap/extension-link", "@tiptap/extension-placeholder",
    "@tiptap/extension-underline",
]

# Packages whose ESM is not at dist/index.js. `.mjs` is renamed on the way in:
# production nginx has no .mjs mime type and the browser refuses the module.
ODD = {
    "w3c-keyname": "index.js",
    "linkifyjs": "dist/linkify.mjs",
}

PIN = {}
files = {}          # specifier -> (local filename, bytes)
SOURCES = {}        # specifier -> source url


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def version(pkg):
    if pkg in PIN:
        return PIN[pkg]
    if pkg.startswith("@tiptap/"):
        PIN[pkg] = TIPTAP
        return TIPTAP
    meta = json.loads(fetch(f"https://data.jsdelivr.com/v1/packages/npm/{pkg}"))
    PIN[pkg] = meta["tags"]["latest"]
    return PIN[pkg]


def split(spec):
    if spec.startswith("@"):
        parts = spec.split("/")
        return "/".join(parts[:2]), parts[2:]
    parts = spec.split("/")
    return parts[0], parts[1:]


def subpath(pkg, rest):
    if not rest and pkg in ODD:
        return ODD[pkg]
    if rest:
        return "/".join(rest) + "/dist/index.js"
    return "dist/index.js"


def local_name(spec):
    return spec.replace("@", "").replace("/", "__") + ".js"


def walk(spec):
    if spec in files:
        return
    pkg, rest = split(spec)
    url = f"{CDN}{pkg}@{version(pkg)}/{subpath(pkg, rest)}"
    body = fetch(url).decode("utf-8")
    # The .map is not vendored and ResilientManifestStaticFilesStorage would
    # rather not see the reference at all. Line-anchored, like the pixi note.
    body = re.sub(r"(?m)^//# sourceMappingURL=.*$\n?", "", body)
    files[spec] = (local_name(spec), body)
    SOURCES[spec] = url
    for dep in sorted(set(re.findall(r"""from\s*["']([^"'./][^"']*)["']""", body))):
        walk(dep)


for entry in ENTRIES:
    walk(entry)

DEST.mkdir(parents=True, exist_ok=True)
for spec, (name, body) in files.items():
    (DEST / name).write_text(body, encoding="utf-8")

manifest = {spec: files[spec][0] for spec in sorted(files)}
(DEST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

total = sum(len(b.encode()) for _, b in files.values())
print(f"{len(files)} files, {total // 1024} KB -> {DEST}")
for spec in sorted(files):
    print(f"  {spec:44s} {SOURCES[spec]}")
