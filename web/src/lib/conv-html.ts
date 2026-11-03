// A thread as a standalone HTML page ("download conversation") — the shareable
// twin of conv-text.ts's markdown. PURE: the markdown renderer is injected.
//
// Same scope and system-prompt rule as conversationText. One file, no scripts;
// the only external reference is KaTeX's stylesheet, linked only when a message
// rendered math. Thinking travels folded; an opening system prompt longer than
// FOLD_SYSTEM chars is folded too (recorded calls open with ~45k of scaffold).

import { isInstruction, type TreeNode } from './tree.ts';
import { encryptedNote, isSealed, thinkingHeader } from './reasoning.ts';

const FOLD_SYSTEM = 1500;

export type ConvHtmlOpts = {
  /** Markdown → HTML (escapes raw HTML; renders math). */
  render: (md: string) => string;
  /** Page title / heading: the thread's name, else the workspace's. */
  title: string;
  /** Meta line parts (workspace, panel, model…), shown under the title. */
  meta: string[];
  workspaceSystem?: string;
  katexVersion?: string;
  exportedAt?: Date;
};

export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function card(role: string, label: string, body: string): string {
  return `<section class="msg ${role}"><div class="role">${escapeHtml(label)}</div>${body}</section>`;
}

function systemCard(label: string, text: string, render: (md: string) => string): string {
  const html = `<div class="body">${render(text)}</div>`;
  if (text.length <= FOLD_SYSTEM) return card('system', label, html);
  const n = text.length.toLocaleString('en-US');
  return `<section class="msg system"><details><summary><span class="role">${escapeHtml(label)}</span> <span class="muted">${n} characters</span></summary>${html}</details></section>`;
}

export function conversationHtml(path: TreeNode[], opts: ConvHtmlOpts): string {
  const { render } = opts;
  const parts: string[] = [];
  const root = path[0];
  if (!isInstruction(root?.role)) {
    const system = root?.system_prompt?.trim() ? root.system_prompt : opts.workspaceSystem?.trim() ? opts.workspaceSystem : '';
    if (system) parts.push(systemCard('System', system, render));
  }
  for (const n of path) {
    if (isInstruction(n.role)) {
      parts.push(systemCard(n.role === 'system' ? 'System' : 'Developer', n.content, render));
    } else if (n.role === 'user') {
      parts.push(card('user', 'User', `<div class="body">${render(n.content)}</div>`));
    } else if (n.role === 'tool') {
      parts.push(card('tool', n.name ? `Tool · ${n.name}` : 'Tool', `<pre class="body">${escapeHtml(n.content)}</pre>`));
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
      for (const c of n.tool_calls ?? [])
        body.push(`<pre class="call">→ ${escapeHtml(c.function.name)}(${escapeHtml(c.function.arguments)})</pre>`);
      parts.push(card('assistant', 'Assistant', body.join('')));
    }
  }
  const main = parts.join('\n');
  const katex =
    opts.katexVersion && main.includes('class="katex')
      ? `<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@${opts.katexVersion}/dist/katex.min.css">\n`
      : '';
  const when = (opts.exportedAt ?? new Date()).toISOString().slice(0, 16).replace('T', ' ');
  const meta = [...opts.meta.filter(Boolean), `exported ${when} UTC`].map(escapeHtml).join(' · ');
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
<header><h1>${escapeHtml(opts.title)}</h1><p class="meta">${meta}</p></header>
${main}
<footer>Exported from tinkerscope</footer>
</main>
</body>
</html>
`;
}

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
header { margin-bottom: 24px; }
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
.msg.system details > summary .role { margin-bottom: 0; }
.msg.system details[open] > summary { margin-bottom: 8px; }
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
