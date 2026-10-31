// A thread as markdown for the clipboard ("copy conversation"). PURE.
//
// The system prompt the thread runs under comes first, whichever place it lives:
// a system row (a recorded call's system message), the thread prompt on the first
// message, or — when neither — the workspace prompt the caller says is active.
// Tool calls and tool results are part of the conversation, so they're written too.

import type { TreeNode } from './tree.ts';

export function conversationText(
  path: TreeNode[],
  opts: { withThinking?: boolean; workspaceSystem?: string } = {}
): string {
  const parts: string[] = [];
  const root = path[0];
  const system =
    root?.role === 'system' ? root.content : root?.system_prompt?.trim() ? root.system_prompt : opts.workspaceSystem?.trim() ? opts.workspaceSystem : '';
  if (system) parts.push(`## System\n\n${system}`);
  for (const n of path) {
    if (n.role === 'system') continue;
    if (n.role === 'user') {
      parts.push(`## User\n\n${n.content}`);
    } else if (n.role === 'tool') {
      parts.push(`## Tool${n.name ? ` (${n.name})` : ''}\n\n${n.content}`);
    } else {
      const body: string[] = [];
      if (opts.withThinking && n.reasoning?.trim()) body.push(`<think>\n${n.reasoning}\n</think>`);
      if (n.content) body.push(n.content);
      for (const c of n.tool_calls ?? []) body.push(`→ ${c.function.name}(${c.function.arguments})`);
      parts.push(`## Assistant\n\n${body.join('\n\n')}`);
    }
  }
  return parts.join('\n\n');
}
