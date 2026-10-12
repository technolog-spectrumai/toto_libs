// Reading a CSV file into rows (2026-10-12). New here: Morion writes CSV
// (formats/csv.ts) and never reads one. The rules are that writer's, read
// backwards (RFC 4180): a field in double quotes may hold the separator, a
// line break and a doubled quote; everything else is taken as written.
//
// The separator is the file's own: a comma, a semicolon or a tab, whichever
// the first line has most of outside quotes (a spreadsheet in a locale with a
// decimal comma writes semicolons). A byte order mark is skipped. Nothing is
// guessed about a cell beyond this: a field that is a plain number becomes a
// number, so it sorts and sums as one; "007" and "1e5" stay text.

export const MAX_CSV_ROWS = 20_000;
export const MAX_CSV_COLUMNS = 500;

export interface CsvRead {
  rows: Array<Array<string | number>>;
  separator: string;
  /** Rows and columns past the limits, left out. */
  rowsLeftOut: number;
  columnsLeftOut: number;
}

const PLAIN_NUMBER = /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;

export function sniffSeparator(text: string): string {
  const counts: Record<string, number> = { ",": 0, ";": 0, "\t": 0 };
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === '"') quoted = !quoted;
    else if (!quoted && (ch === "\n" || ch === "\r")) break;
    else if (!quoted && ch in counts) counts[ch] += 1;
  }
  let best = ",";
  for (const sep of [";", "\t"]) if (counts[sep] > counts[best]) best = sep;
  return best;
}

export function readCsv(source: string): CsvRead {
  const text = source.charCodeAt(0) === 0xfeff ? source.slice(1) : source;
  const separator = sniffSeparator(text);
  const rows: Array<Array<string | number>> = [];
  let row: Array<string | number> = [];
  let field = "";
  let quoted = false;
  let wasQuoted = false;
  let rowsLeftOut = 0;
  let columnsLeftOut = 0;

  const endField = () => {
    if (row.length < MAX_CSV_COLUMNS) {
      row.push(!wasQuoted && field.length < 16 && PLAIN_NUMBER.test(field) ? Number(field) : field);
    } else columnsLeftOut += 1;
    field = "";
    wasQuoted = false;
  };
  const endRow = () => {
    endField();
    if (rows.length < MAX_CSV_ROWS) rows.push(row);
    else rowsLeftOut += 1;
    row = [];
  };

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i += 1; } else quoted = false;
      } else field += ch;
    } else if (ch === '"' && field === "") { quoted = true; wasQuoted = true; }
    else if (ch === separator) endField();
    else if (ch === "\n") endRow();
    else if (ch === "\r") { if (text[i + 1] === "\n") i += 1; endRow(); }
    else field += ch;
  }
  // The last line, when the file does not end with a line break.
  if (field !== "" || wasQuoted || row.length) endRow();
  return { rows, separator, rowsLeftOut, columnsLeftOut };
}
