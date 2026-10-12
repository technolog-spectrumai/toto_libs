// `.uson` — the platform's sheet file — as the web host reads and writes it.
//
// A `.uson` is ONE JSON object, an envelope around a Univer workbook snapshot
// (`IWorkbookData`, what `workbook.save()` returns):
//
//     {"format": "uson", "version": 1, "workbook": { … }}
//
// It is the platform's format (zenobia, 2026-10-02: `toto/primula/uson.py`),
// made so that a sheet says what it is in its name and in its first key, and
// no JSON file is ever guessed to be one from its shape. The rules below are
// that module's, one for one.
//
// THE MAPPING is no conversion. The envelope's `workbook` is the very thing
// Morion's sheet editor works on — `formats/sheet.ts` is a port of the module
// the platform later put the envelope around — so:
//
//   open   the host reads the file, checks the envelope (`openUson`) and hands
//          `SheetEditor` the workbook alone, as the text it reads;
//   save   the editor answers with a workbook's text, and the host writes the
//          file again around it (`wrapUson`).
//
// The editor is the desktop's, unchanged: it never sees an envelope, and its
// own check (`isSheet`) passes on everything `openUson` lets through.
//
// WHAT A SAVE KEEPS.
//
//   * Nothing changed — the editor answers the workbook it was handed — and
//     the file goes back as it was read, byte for byte: its spacing, the
//     order of its keys, a byte order mark.
//   * Something changed, and the file is written the way the platform writes
//     one: Python's `json.dumps(…, ensure_ascii=False)`, which is what
//     `formats/sheet.ts` already writes a workbook as. The envelope's keys
//     keep the file's order, and a key this build does not know is carried
//     through with its value. So a `.uson` the platform wrote, or this page
//     did, reads back and saves without a byte moving but those of the edit.
//
// What a save does NOT keep is the editor's to lose, and the same as for a
// workbook on the desktop: Univer fills in defaults of its own when it loads
// a snapshot, JavaScript orders an object's whole-number keys, and a number
// Python wrote as `1.0` comes back as `1` (see `dumps` in `formats/sheet.ts`).
//
// AN EMPTY FILE is a new sheet, by the editors' own rule: `SheetEditor` seeds
// a blank workbook named after the file, and the first save writes it in a
// new envelope (`newUson`, which is also what the platform's New makes).
import { extensionOf } from "./ext";
import { dumps, newWorkbook, type Workbook } from "./sheet";

export const USON_EXTENSION = "uson";
export const USON_FORMAT = "uson";
export const USON_VERSION = 1;

/** A workbook with more tabs than this is refused, as the platform refuses
 *  it: nobody edits one, and the whole of it is in memory. */
export const MAX_SHEETS = 200;

/** Why a file is not a `.uson` this build reads, in the order the reasons are
 *  met. The words are the host's (`describeUsonRefusal`, ./messages.ts). */
export type UsonRefusal =
  | { reason: "not-json" }
  | { reason: "not-uson" }
  /** `version` is the file's own, as JSON, shortened. */
  | { reason: "version"; version: string }
  | { reason: "no-workbook" }
  | { reason: "no-sheets" }
  | { reason: "too-many-sheets"; count: number }
  | { reason: "bad-sheet" };

export class UsonError extends Error {
  readonly refusal: UsonRefusal;
  constructor(refusal: UsonRefusal) {
    super(`Not a .uson sheet this build reads (${refusal.reason}).`);
    this.name = "UsonError";
    this.refusal = refusal;
  }
}

/** A `.uson` that was opened: what the sheet editor gets, and what a save
 *  needs to write the file again. */
export interface UsonFile {
  /** The file's own text, which goes back when nothing changed. Null for an
   *  empty file: there is nothing of it to keep. */
  readonly source: string | null;
  /** The workbook as `SheetEditor` is handed it — `dumps` of what the
   *  envelope holds. Empty for an empty file, which the editor seeds. */
  readonly sheet: string;
  /** The envelope as parsed, for its keys and their order. Null for an empty
   *  file. */
  readonly envelope: Readonly<Record<string, unknown>> | null;
}

/** Whether a routing name says `.uson`. */
export function isUsonName(name: string): boolean {
  return extensionOf(name) === USON_EXTENSION;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Any JSON value, spaced as Python spaces it. `dumps` is typed for the one
 *  caller it had, a workbook; what it does is not particular to one. */
function python(value: unknown): string {
  return dumps(value as Workbook);
}

/** The most of a wrong `version` that the refusal quotes. */
const QUOTED = 40;

/**
 * Read a `.uson` file's text.
 *
 * Throws `UsonError` for anything the platform would refuse to save as one:
 * text that is not JSON, an object that does not say `"format": "uson"`,
 * another `version` than 1 (`true` is not 1), a `workbook` that is not an
 * object, one without `sheets` (an object) and `sheetOrder` (a list), more
 * than `MAX_SHEETS` sheets, a sheet that is not an object. A byte order mark
 * at the start is not a refusal.
 */
export function openUson(text: string): UsonFile {
  // `trim` takes a byte order mark too: a file of nothing else is empty.
  if (!text.trim()) return { source: null, sheet: "", envelope: null };

  let parsed: unknown;
  try {
    parsed = JSON.parse(text.charCodeAt(0) === 0xfeff ? text.slice(1) : text);
  } catch {
    throw new UsonError({ reason: "not-json" });
  }
  if (!isObject(parsed) || parsed.format !== USON_FORMAT) throw new UsonError({ reason: "not-uson" });
  if (parsed.version !== USON_VERSION) {
    const version = JSON.stringify(parsed.version ?? null) ?? "null";
    throw new UsonError({ reason: "version", version: version.slice(0, QUOTED) });
  }
  const workbook = parsed.workbook;
  if (!isObject(workbook)) throw new UsonError({ reason: "no-workbook" });
  const sheets = workbook.sheets;
  if (!isObject(sheets) || !Array.isArray(workbook.sheetOrder)) throw new UsonError({ reason: "no-sheets" });
  const count = Object.keys(sheets).length;
  if (count > MAX_SHEETS) throw new UsonError({ reason: "too-many-sheets", count });
  if (!Object.values(sheets).every(isObject)) throw new UsonError({ reason: "bad-sheet" });

  let sheet: string;
  try {
    sheet = python(workbook);
  } catch {
    // Nested deeper than a call stack: the platform's own reader gives up on
    // such a file as "not valid JSON", and so does this one.
    throw new UsonError({ reason: "not-json" });
  }
  return { source: text, sheet, envelope: parsed };
}

/** The envelope of a file that has none yet. */
const NEW_ENVELOPE: Readonly<Record<string, unknown>> = {
  format: USON_FORMAT, version: USON_VERSION, workbook: null,
};

/**
 * The text of the `.uson` that holds `sheet` — a workbook as the sheet editor
 * gives it back — in the envelope `file` was opened from.
 *
 * `sheet` is written in as it stands. It is the editor's `dumps` of a
 * snapshot, which is JSON spaced the way the rest of the file is; the
 * platform checks the whole when it is saved.
 */
export function wrapUson(sheet: string, file: UsonFile): string {
  if (file.source !== null && sheet === file.sheet) return file.source;
  const envelope = file.envelope ?? NEW_ENVELOPE;
  const fields = Object.keys(envelope).map((key) =>
    `${JSON.stringify(key)}: ${key === "workbook" ? sheet : python(envelope[key])}`);
  return `{${fields.join(", ")}}`;
}

/** A new `.uson`: one empty sheet in a workbook called `name`. What the host
 *  writes for a file that was empty, and what the platform's New starts a
 *  `.uson` with (its copy is pinned against this one's output). */
export function newUson(name: string): string {
  return wrapUson(dumps(newWorkbook(name)), { source: null, sheet: "", envelope: null });
}
