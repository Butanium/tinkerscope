// Enforces the house tooltip rule (CLAUDE.md): a tooltip is ONE short line
// naming what the control does (~70 chars). A fat one renders as a slab over the
// UI and nobody reads it twice — mechanism, modifier tables and caveats belong in
// the `?` modal. The rule was written down and unenforced, and six offenders had
// accreted quietly; this is the write-time check.
//   node web/src/lib/tooltip-length.test.ts
//
// Scans every `data-tooltip=` in web/src/**/*.svelte. A `{...}` value is measured
// PER STRING LITERAL, so a ternary is judged by the branch that actually renders,
// not by its source length. `${...}` inside a template literal counts as one
// character — the check is about authored prose, and an interpolated value's
// width isn't knowable here.
// @ts-ignore  the only node-builtin importer in web/src; not worth an @types/node
// devDependency for one lint (svelte-check would otherwise error on these two).
import { readdirSync, readFileSync, statSync } from 'node:fs';
// @ts-ignore  (same)
import { join } from 'node:path';

/** ~70 is the target; 90 is where it fails, so the guidance keeps some slack. */
const MAX = 90;

const SRC = new URL('..', import.meta.url).pathname;

function svelteFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...svelteFiles(p));
    else if (name.endsWith('.svelte')) out.push(p);
  }
  return out;
}

/** Read the balanced `{...}` starting at `i` (which points at the `{`), skipping
 *  braces that live inside a string. Returns the expression source. */
function braceExpr(src: string, i: number): string {
  let depth = 0;
  let quote: string | null = null;
  for (let j = i; j < src.length; j++) {
    const c = src[j];
    if (quote) {
      if (c === '\\') j++;
      else if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'" || c === '`') quote = c;
    else if (c === '{') depth++;
    else if (c === '}' && --depth === 0) return src.slice(i + 1, j);
  }
  return src.slice(i + 1);
}

/** Read a quoted attribute value starting at `i` (the opening quote). */
function quoted(src: string, i: number): string {
  const q = src[i];
  const end = src.indexOf(q, i + 1);
  return end < 0 ? src.slice(i + 1) : src.slice(i + 1, end);
}

/** Every string literal in an expression — each is a tooltip a user can see.
 *  `at` is the literal's offset in `expr`, so a failure names the branch's line. */
function literals(expr: string): { text: string; at: number }[] {
  const out: { text: string; at: number }[] = [];
  for (let i = 0; i < expr.length; i++) {
    const q = expr[i];
    if (q !== '"' && q !== "'" && q !== '`') continue;
    let buf = '';
    let j = i + 1;
    for (; j < expr.length; j++) {
      const c = expr[j];
      if (c === '\\') {
        buf += expr[++j] ?? '';
        continue;
      }
      if (c === q) break;
      // an interpolated value is one unknown-width slot, counted as one char
      if (q === '`' && c === '$' && expr[j + 1] === '{') {
        let depth = 0;
        for (; j < expr.length; j++) {
          if (expr[j] === '{') depth++;
          else if (expr[j] === '}' && --depth === 0) break;
        }
        buf += '…';
        continue;
      }
      buf += c;
    }
    out.push({ text: buf, at: i });
    i = j;
  }
  return out;
}

/** Tooltip strings a file can render, each with the line it sits on. */
function tooltips(src: string): { text: string; line: number }[] {
  const out: { text: string; line: number }[] = [];
  const lineAt = (off: number) => src.slice(0, off).split('\n').length;
  const KEY = 'data-tooltip=';
  for (let i = src.indexOf(KEY); i >= 0; i = src.indexOf(KEY, i + 1)) {
    const v = i + KEY.length;
    if (src[v] === '{') {
      for (const { text, at } of literals(braceExpr(src, v))) out.push({ text, line: lineAt(v + 1 + at) });
    } else if (src[v] === '"' || src[v] === "'") {
      out.push({ text: quoted(src, v), line: lineAt(v) });
    }
  }
  return out;
}

const offenders: string[] = [];
let checked = 0;
for (const file of svelteFiles(SRC)) {
  const rel = file.slice(SRC.length);
  for (const { text, line } of tooltips(readFileSync(file, 'utf8'))) {
    checked++;
    // A tooltip is ONE line: a newline in the rendered text is a slab by itself.
    if (text.includes('\n')) offenders.push(`${rel}:${line} — multi-line tooltip: ${JSON.stringify(text)}`);
    else if (text.length > MAX)
      offenders.push(`${rel}:${line} — ${text.length} chars (max ${MAX}): ${JSON.stringify(text)}`);
  }
}

if (offenders.length) {
  console.error(
    `✗ tooltip-length: ${offenders.length} tooltip(s) over ${MAX} chars.\n` +
      `  Tooltips are ONE short line (~70 chars) — move the mechanism into the ? modal (HelpModal.svelte).\n` +
      offenders.map((o) => `    ${o}`).join('\n')
  );
  // Throw rather than process.exit: same non-zero exit for the runner, no `process`
  // (and so no @types/node) needed.
  throw new Error(`tooltip-length: ${offenders.length} tooltip(s) over ${MAX} chars`);
}
console.log(`✓ tooltip-length: ${checked} tooltips, none over ${MAX} chars`);
