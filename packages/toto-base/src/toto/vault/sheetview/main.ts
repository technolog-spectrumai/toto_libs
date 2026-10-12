// The sheet viewer's entry for a plain page (2026-10-12): Morion's sheet
// format (sheet.ts), its `.uson` reader (uson.ts) and the grid it mounts
// (Univer, as editors/SheetEditor.vue does), without Vue and READ ONLY: the
// workbook is not editable, there is no toolbar, no formula bar, no context
// menu and no save path. A CSV file is read into rows (csvRead.ts) and shown
// as a one-sheet workbook (`workbookFromRows`).
import { createUniver, LocaleType, merge } from "@univerjs/presets";
import { UniverSheetsCorePreset } from "@univerjs/preset-sheets-core";
import enUS from "@univerjs/preset-sheets-core/locales/en-US";
import "@univerjs/preset-sheets-core/lib/index.css";
import { readCsv } from "./csvRead";
import { isSheet, loads, workbookFromRows, type Workbook } from "./sheet";
import { openUson, UsonError } from "./uson";

export interface Opened {
  workbook: Workbook | null;
  /** "" | "empty" | "not-a-sheet" | a `.uson` refusal's reason */
  refusal: string;
  rowsLeftOut: number;
  columnsLeftOut: number;
}

/** A file's text as a workbook: `kind` is "csv", "uson" or "sheet" (a bare
 *  Univer snapshot, the legacy `.json` sheet). */
export function open(kind: string, name: string, text: string): Opened {
  const none = { workbook: null, rowsLeftOut: 0, columnsLeftOut: 0 };
  if (!text.trim()) return { ...none, refusal: "empty" };
  if (kind === "csv") {
    const read = readCsv(text);
    const workbook = workbookFromRows(name, read.rows, "Sheet1");
    const sheet = workbook.sheets[workbook.sheetOrder[0]] as Record<string, unknown>;
    // Room for the whole file: the blank workbook has 100 rows and 26 columns.
    const widest = read.rows.reduce((most, row) => Math.max(most, row.length), 0);
    sheet.rowCount = Math.max(100, read.rows.length + 20);
    sheet.columnCount = Math.max(26, widest + 5);
    return { workbook, refusal: "", rowsLeftOut: read.rowsLeftOut, columnsLeftOut: read.columnsLeftOut };
  }
  if (kind === "uson") {
    try {
      const file = openUson(text);
      if (!file.sheet) return { ...none, refusal: "empty" };
      return { ...none, workbook: loads(file.sheet), refusal: "" };
    } catch (err) {
      if (err instanceof UsonError) return { ...none, refusal: err.refusal.reason };
      throw err;
    }
  }
  if (!isSheet(text)) return { ...none, refusal: "not-a-sheet" };
  return { ...none, workbook: loads(text), refusal: "" };
}

/** Mounts the grid in `el`, which must have a size. Returns its disposer. */
export function mount(el: HTMLElement, workbook: Workbook, dark: boolean): () => void {
  const { univerAPI, univer } = createUniver({
    locale: LocaleType.EN_US,
    locales: { [LocaleType.EN_US]: merge({}, (enUS as { default?: unknown }).default ?? enUS) },
    darkMode: dark,
    presets: [UniverSheetsCorePreset({
      container: el, header: false, toolbar: false, formulaBar: false, contextMenu: false, footer: true,
    } as never)],
  });
  const book = univerAPI.createWorkbook(workbook as never) as unknown as { setEditable?(on: boolean): unknown };
  // A viewer: nothing typed into a cell is kept, and nothing can be typed.
  try { book.setEditable?.(false); } catch { /* an older facade: the page has no save path either way */ }
  return () => { try { univer.dispose(); } catch { /* a grid that will not dispose */ } };
}
