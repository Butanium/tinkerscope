// Run: node web/src/lib/conv-text.test.ts
import { conversationText } from './conv-text.ts';
import type { TreeNode } from './tree.ts';

let failed = 0;
function eq(a: unknown, b: unknown, name: string): void {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    failed++;
    console.log(`✗ ${name}\n    expected ${JSON.stringify(b)}\n    got      ${JSON.stringify(a)}`);
  }
}
const node = (n: Partial<TreeNode>): TreeNode => ({ id: 'x', parent: null, children: [], content: '', role: 'user', ...n }) as TreeNode;

const call = { id: 'c1', type: 'function' as const, function: { name: 'send', arguments: '{"m":"hi"}' } };
const path = [
  node({ role: 'system', content: 'be Pip' }),
  node({ role: 'user', content: 'q' }),
  node({ role: 'assistant', content: '', reasoning: 'hmm', tool_calls: [call] }),
  node({ role: 'tool', name: 'send', content: 'sent' })
];
eq(conversationText(path), '## System\n\nbe Pip\n\n## User\n\nq\n\n## Assistant\n\n→ send({"m":"hi"})\n\n## Tool (send)\n\nsent', 'system row + calls + tool');
eq(conversationText(path, { withThinking: true }).includes('<think>\nhmm\n</think>\n\n→ send'), true, 'thinking before the call');
eq(conversationText([node({ content: 'q', system_prompt: 'thread one' })], { workspaceSystem: 'ws' }), '## System\n\nthread one\n\n## User\n\nq', 'thread prompt wins');
eq(conversationText([node({ content: 'q' })], { workspaceSystem: 'ws' }), '## System\n\nws\n\n## User\n\nq', 'workspace prompt when the thread has none');
eq(conversationText([node({ content: 'q' })]), '## User\n\nq', 'no prompt at all');

if (failed) throw new Error(`${failed} failed`);
console.log('conv-text.test.ts: all passed');
