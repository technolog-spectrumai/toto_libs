// The sheet format — a Univer workbook snapshot (`IWorkbookData`) stored as
// JSON in an ordinary `.json` file. (Carried over from the Primula app.)
//
// A PORT of portal/vendor/toto_libs/packages/toto-works/src/toto/primula/sheet_format.py,
// and by far the thinnest of the four: the server builds a blank workbook,
// sniffs whether some JSON is one, and round-trips the text. It never inspects
// a cell. That makes the format genuinely client-owned — unlike documents and
// decks, there is no server-side sanitiser whose allowlist we must match.
//
// The one thing to keep faithful is `appVersion`. It records which Univer wrote
// the snapshot, and a file written by a newer grid than the one reading it is
// the failure mode this field exists to make visible.

/** The Univer build primula pins. Kept in step with the npm packages below;
 *  a mismatch is worth knowing about, not worth refusing a file over. */
export const APP_VERSION = "0.25.1";

/** Univer's CellValueType. */
export const CELL_TYPE = { string: 1, number: 2, boolean: 3 } as const;

/** Deliberately loose: this is Univer's schema, not ours, and typing it in full
 *  here would mean re-declaring a third-party model we only ever pass through. */
export interface Workbook {
  id: string;
  name: string;
  appVersion: string;
  locale: string;
  sheetOrder: string[];
  styles: Record<string, unknown>;
  sheets: Record<string, unknown>;
  resources: unknown[];
  [key: string]: unknown;
}

function shortId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(4)))
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** A minimal, valid Univer workbook snapshot with one empty sheet. */
export function newWorkbook(name = "New sheet"): Workbook {
  const sid = "sheet-1";
  return {
    id: `wb-${shortId()}`,
    name: name || "New sheet",
    appVersion: APP_VERSION,
    locale: "enUS",
    sheetOrder: [sid],
    styles: {},
    sheets: {
      [sid]: {
        id: sid,
        name: "Sheet1",
        tabColor: "",
        rowCount: 100,
        columnCount: 26,
        cellData: {},
        rowData: {},
        columnData: {},
      },
    },
    resources: [],
  };
}

/** True if `text` is a Univer workbook snapshot (has `sheets` + `sheetOrder`).
 *
 *  The cheap identity check that lets an ordinary json/sheet vault file be
 *  recognised as a sheet — the same idea as the document and deck sniffs, but
 *  this one has to parse, because JSON has no root element to look at. */
export function isSheet(text: string): boolean {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return false;
  }
  if (typeof data !== "object" || data === null || Array.isArray(data)) return false;
  const d = data as Record<string, unknown>;
  return typeof d.sheets === "object" && d.sheets !== null
    && !Array.isArray(d.sheets) && "sheetOrder" in d;
}

/** Build a workbook from a 2D list of cell values — for seeds and tests.
 *  Empty cells (null/"") are skipped, exactly as Python does. */
export function workbookFromRows(
  name: string, rows: Array<Array<string | number | boolean | null>>, sheetName = "Sheet1",
): Workbook {
  const cellData: Record<string, Record<string, unknown>> = {};
  rows.forEach((row, r) => {
    const colMap: Record<string, unknown> = {};
    row.forEach((val, c) => {
      if (val === null || val === undefined || val === "") return;
      if (typeof val === "boolean") colMap[String(c)] = { v: val, t: CELL_TYPE.boolean };
      else if (typeof val === "number") colMap[String(c)] = { v: val, t: CELL_TYPE.number };
      else colMap[String(c)] = { v: String(val), t: CELL_TYPE.string };
    });
    if (Object.keys(colMap).length) cellData[String(r)] = colMap;
  });

  const wb = newWorkbook(name);
  const sid = wb.sheetOrder[0];
  const sheet = wb.sheets[sid] as Record<string, unknown>;
  sheet.name = sheetName;
  sheet.cellData = cellData;
  return wb;
}

/** Serialise the way Python's `json.dumps(..., ensure_ascii=False)` does.
 *
 *  This is not pedantry. `primula.sheet_save` parses the client's body and
 *  RE-SERIALISES it with Python, so the bytes on disk carry Python's default
 *  `", "` / `": "` separators. `JSON.stringify` emits neither, so a zinnia save
 *  (which writes verbatim through the content API) would differ from a web save
 *  of the identical workbook by whitespace alone — every alternating save
 *  showing a diff for a file nobody changed.
 *
 *  Leaf values go through `JSON.stringify`, so string escaping and number
 *  formatting stay exactly what the platform already agrees on.
 *
 *  Known and accepted difference: Python distinguishes `1.0` (float) from `1`
 *  (int) and writes them differently; JavaScript has one number type, so a
 *  Python-written integral FLOAT re-serialises here as an integer. Univer only
 *  ever writes JS numbers, so this cannot arise from an edit — only from a
 *  hand-written or seeded file, and only cosmetically. */
export function dumps(workbook: Workbook): string {
  return pythonJson(workbook);
}

function pythonJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) {
    return "[" + value.map(pythonJson).join(", ") + "]";
  }
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v !== undefined) // JSON.stringify drops these; so does Python
    .map(([k, v]) => `${JSON.stringify(k)}: ${pythonJson(v)}`);
  return "{" + entries.join(", ") + "}";
}

export function loads(text: string): Workbook {
  return JSON.parse(text) as Workbook;
}

/** The used range as TSV — what a spreadsheet range IS to anything outside the
 *  grid, and what somebody would paste into one. Kept here rather than in the
 *  editor because the assistant, an export and a preview all want the same
 *  answer. */
export function cellsToTsv(values: Array<Array<unknown>>): string {
  return values
    .map((row) => row.map((cell) => (cell === null || cell === undefined ? "" : String(cell))).join("\t"))
    .join("\n")
    .trim();
}
