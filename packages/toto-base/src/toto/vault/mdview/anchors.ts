// A heading's address, by zenobia's rule (`toto.htmlview.markdown_source`):
// Django's `slugify`, at most 60 characters, "section" when nothing is left,
// and a repeat numbered `a`, `a_1`, `a_2`, …. The same heading text has the
// same address in both apps, so a link written for one works in the other.

const MAX_ANCHOR = 60;

/** Django's `slugify` for ASCII output: accents are dropped with their
 *  letter's plain form kept (`ż` → `z`), a letter with none (`ł`) goes. */
export function headingAnchor(value: string): string {
  const ascii = value.normalize("NFKD").replace(/[^\x00-\x7f]/g, "");
  const slug = ascii
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/[-\s]+/g, "-")
    .replace(/^[-_]+|[-_]+$/g, "");
  return slug.slice(0, MAX_ANCHOR) || "section";
}

const NUMBERED = /^(.*)_([0-9]+)$/;

/** `headingAnchor` for one conversion, with repeats made unique. A taken
 *  anchor gets the first free of `a_1`, `a_2`, …; a taken `a_3` continues
 *  from `a_4`. Each handed-out `base_n` remembers the next number that may
 *  still be free, so thousands of identical headings cost one step each. */
export class HeadingAnchors {
  private taken = new Set<string>();
  private after = new Map<string, number>();

  /** Mark an id that is not a heading's (a footnote's) as taken. */
  reserve(id: string): void {
    this.taken.add(id);
  }

  next(value: string): string {
    let anchor = headingAnchor(value);
    if (this.taken.has(anchor)) {
      const numbered = NUMBERED.exec(anchor);
      const base = numbered ? numbered[1] : anchor;
      const from = numbered ? Number(numbered[2]) + 1 : 1;
      anchor = `${base}_${this.free(base, from)}`;
    }
    this.taken.add(anchor);
    const numbered = NUMBERED.exec(anchor);
    // `a_01` is no `a_1`: never a candidate, so it points nowhere.
    if (numbered && numbered[2] === String(Number(numbered[2]))) {
      this.after.set(`${numbered[1]}\u0000${numbered[2]}`, Number(numbered[2]) + 1);
    }
    return anchor;
  }

  /** The first number from `from` on whose `base_n` is not taken. */
  private free(base: string, from: number): number {
    const passed: number[] = [];
    let number = from;
    for (;;) {
      const next = this.after.get(`${base}\u0000${number}`);
      if (next === undefined) {
        if (!this.taken.has(`${base}_${number}`)) break;
        number += 1;
        continue;
      }
      passed.push(number);
      number = next;
    }
    for (const step of passed) this.after.set(`${base}\u0000${step}`, number);
    return number;
  }
}
