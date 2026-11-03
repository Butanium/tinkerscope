// A thread as a standalone HTML page ("download conversation") — the shareable
// twin of conv-text.ts's markdown. PURE: the markdown renderer is injected.
//
// Same scope and system-prompt rule as conversationText. One file: a few lines of
// inline script for the copy / download buttons (the markdown and each message's
// raw text ride in a JSON block); the only external reference is KaTeX's
// stylesheet, linked only when a message rendered math. Thinking travels folded;
// an opening system prompt longer than FOLD_SYSTEM chars is folded too (recorded
// calls open with ~45k of scaffold).

import { isInstruction, type TreeNode } from './tree.ts';
import { encryptedNote, isSealed, thinkingHeader } from './reasoning.ts';
import { conversationText } from './conv-text.ts';

const FOLD_SYSTEM = 1500;

export type ConvHtmlOpts = {
  /** Markdown → HTML (escapes raw HTML; renders math). */
  render: (md: string) => string;
  /** Page title / heading: the thread's name, else the workspace's. */
  title: string;
  /** Meta line parts (workspace, panel, model…), shown under the title. */
  meta: string[];
  /** The page's download name without extension — the markdown saves as `<fileBase>.md`. */
  fileBase?: string;
  workspaceSystem?: string;
  katexVersion?: string;
  exportedAt?: Date;
};

export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

const ICON_COPY =
  '<svg class="i-copy" width="14" height="14" viewBox="0 0 16 16" fill="none"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5" stroke="currentColor" stroke-width="1.4"/><path d="M10.5 3.5v-.5a1.5 1.5 0 0 0-1.5-1.5H4A1.5 1.5 0 0 0 2.5 3v5A1.5 1.5 0 0 0 4 9.5h.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>';
const ICON_CHECK =
  '<svg class="i-check" width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3 3 7-7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

/** One message card; `i` indexes its raw text in the page's JSON block. */
function card(role: string, label: string, body: string, i: number): string {
  const copy = `<button class="copy" data-act="copy-msg" data-i="${i}" title="Copy message" aria-label="Copy message">${ICON_COPY}${ICON_CHECK}</button>`;
  return `<section class="msg ${role}">${copy}<div class="role">${escapeHtml(label)}</div>${body}</section>`;
}

function systemBody(text: string, render: (md: string) => string): string {
  const html = `<div class="body">${render(text)}</div>`;
  if (text.length <= FOLD_SYSTEM) return html;
  return `<details class="fold"><summary class="muted">${text.length.toLocaleString('en-US')} characters</summary>${html}</details>`;
}

export function conversationHtml(path: TreeNode[], opts: ConvHtmlOpts): string {
  const { render } = opts;
  const parts: string[] = [];
  const texts: string[] = [];
  const add = (role: string, label: string, body: string, raw: string) => {
    parts.push(card(role, label, body, texts.length));
    texts.push(raw);
  };
  const root = path[0];
  if (!isInstruction(root?.role)) {
    const system = root?.system_prompt?.trim() ? root.system_prompt : opts.workspaceSystem?.trim() ? opts.workspaceSystem : '';
    if (system) add('system', 'System', systemBody(system, render), system);
  }
  for (const n of path) {
    if (isInstruction(n.role)) {
      add('system', n.role === 'system' ? 'System' : 'Developer', systemBody(n.content, render), n.content);
    } else if (n.role === 'user') {
      add('user', 'User', `<div class="body">${render(n.content)}</div>`, n.content);
    } else if (n.role === 'tool') {
      add('tool', n.name ? `Tool · ${n.name}` : 'Tool', `<pre class="body">${escapeHtml(n.content)}</pre>`, n.content);
    } else {
      const body: string[] = [];
      if (n.reasoning?.trim()) {
        body.push(
          `<details class="thinking"><summary>${escapeHtml(thinkingHeader(n.reasoning_tokens))}</summary><div class="body">${render(n.reasoning)}</div></details>`
        );
      } else if (isSealed(n.reasoning_details) || n.reasoning_tokens) {
        body.push(`<div class="muted note">reasoning: ${escapeHtml(encryptedNote(n.reasoning_tokens))}</div>`);
      }
      if (n.content) body.push(`<div class="body">${render(n.content)}</div>`);
      const calls = (n.tool_calls ?? []).map((c) => `→ ${c.function.name}(${c.function.arguments})`);
      for (const c of calls) body.push(`<pre class="call">${escapeHtml(c)}</pre>`);
      add('assistant', 'Assistant', body.join(''), [n.content, ...calls].filter(Boolean).join('\n\n'));
    }
  }
  const main = parts.join('\n');
  const katex =
    opts.katexVersion && main.includes('class="katex')
      ? `<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@${opts.katexVersion}/dist/katex.min.css">\n`
      : '';
  const when = (opts.exportedAt ?? new Date()).toISOString().slice(0, 16).replace('T', ' ');
  const meta = [...opts.meta.filter(Boolean), `exported ${when} UTC`].map(escapeHtml).join(' · ');
  // The markdown is the page's content: thinking included, as <think> blocks.
  const md = conversationText(path, { withThinking: true, workspaceSystem: opts.workspaceSystem });
  // `<` escaped so no message text can close the script element.
  const data = JSON.stringify({ md, texts, file: `${opts.fileBase || 'conversation'}.md` }).replace(/</g, '\\u003c');
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(opts.title)}</title>
${katex}<style>${CSS}</style>
</head>
<body>
<main>
<header>
<div><h1>${escapeHtml(opts.title)}</h1><p class="meta">${meta}</p></div>
<div class="actions"><button class="act" data-act="copy-md">${ICON_COPY}${ICON_CHECK}<span>Copy markdown</span></button><button class="act" data-act="download-md"><svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M8 2.5v8M5 7.5l3 3 3-3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/><path d="M2.5 10v3.5h11V10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg><span>Download .md</span></button></div>
</header>
${main}
<footer>Exported from tinkerscope</footer>
</main>
<script type="application/json" id="conv-data">${data}</script>
<script>${SCRIPT}</script>
</body>
</html>
`;
}

const SCRIPT = `
const D = JSON.parse(document.getElementById('conv-data').textContent);
function flash(b) { b.classList.add('done'); setTimeout(() => b.classList.remove('done'), 1200); }
async function copy(text, b) {
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const t = document.createElement('textarea');
    t.value = text; t.style.position = 'fixed'; t.style.opacity = '0';
    document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove();
  }
  flash(b);
}
document.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-act]');
  if (!b) return;
  if (b.dataset.act === 'copy-msg') copy(D.texts[+b.dataset.i], b);
  else if (b.dataset.act === 'copy-md') copy(D.md, b);
  else if (b.dataset.act === 'download-md') {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([D.md], { type: 'text/markdown' }));
    a.download = D.file; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    flash(b);
  }
});
`;

const CSS = `
:root { --bg: #FAF7F2; --surface: #FFFFFF; --text: #3D3328; --muted: #8a7e72; --border: #e0d8cc; --accent: #9C6644;
  --user: #e8edf5; --assistant: #e8f0e8; --system: #f5ece4; --tool: #f0eee9; --code: rgba(61, 51, 40, 0.06); }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1a1a1a; --surface: #242424; --text: #e8e8e8; --muted: #8f8f8f; --border: #333; --accent: #c4915c;
    --user: #2a3040; --assistant: #1e2e1e; --system: #3a2a1e; --tool: #262626; --code: rgba(255, 255, 255, 0.07); }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.6 -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif; }
main { max-width: 820px; margin: 0 auto; padding: 32px 16px 48px; }
header { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 12px 24px; margin-bottom: 24px; }
.actions { display: flex; gap: 8px; flex-shrink: 0; }
button { font: inherit; color: inherit; cursor: pointer; }
.act { display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface); color: var(--muted); font-size: 0.82rem; }
.act:hover { color: var(--accent); border-color: var(--accent); }
.i-check { display: none; }
.done .i-copy { display: none; }
.done .i-check { display: inline; color: var(--accent); }
.act.done span::after { content: ' ✓'; }
.msg { position: relative; }
.copy { position: absolute; top: 8px; right: 8px; display: inline-flex; padding: 5px; border: none; border-radius: 6px; background: transparent; color: var(--muted); opacity: 0; transition: opacity 0.12s; }
.msg:hover .copy, .copy:focus-visible, .copy.done { opacity: 1; }
.copy:hover { background: var(--code); color: var(--text); }
@media (hover: none) { .copy { opacity: 0.6; } }
h1 { margin: 0 0 4px; font-size: 1.5rem; line-height: 1.3; }
.meta { margin: 0; color: var(--muted); font-size: 0.85rem; }
.msg { margin: 12px 0; padding: 14px 18px; border-radius: 10px; border: 1px solid var(--border); overflow-wrap: anywhere; }
.msg.user { background: var(--user); }
.msg.assistant { background: var(--assistant); }
.msg.system { background: var(--system); }
.msg.tool { background: var(--tool); }
.role { display: inline-block; margin-bottom: 6px; font-size: 0.72rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }
.body > :first-child { margin-top: 0; }
.body > :last-child { margin-bottom: 0; }
details > summary { cursor: pointer; color: var(--muted); }
details.thinking { margin-bottom: 10px; padding: 8px 12px; border-left: 3px solid var(--border); background: var(--code); border-radius: 6px; font-size: 0.92rem; }
details.thinking[open] > summary { margin-bottom: 6px; }
details.fold[open] > summary { margin-bottom: 8px; }
.muted { color: var(--muted); }
.note { margin-bottom: 8px; font-size: 0.85rem; font-style: italic; }
pre { white-space: pre-wrap; margin: 0; font: 0.85rem/1.5 'JetBrains Mono', ui-monospace, monospace; }
pre.call { margin-top: 8px; padding: 8px 10px; background: var(--code); border-radius: 6px; }
code { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 0.88em; background: var(--code); padding: 1px 4px; border-radius: 4px; }
pre code { background: none; padding: 0; }
.body pre { padding: 10px 12px; background: var(--code); border-radius: 6px; overflow-x: auto; }
a { color: var(--accent); }
blockquote { margin: 0; padding-left: 12px; border-left: 3px solid var(--border); color: var(--muted); }
table { border-collapse: collapse; } td, th { border: 1px solid var(--border); padding: 4px 8px; }
footer { margin-top: 32px; color: var(--muted); font-size: 0.8rem; text-align: center; }
`;
