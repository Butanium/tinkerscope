// A pack's token-logprob encoding — the browser half.
//
// In a PACK, per-token logprobs travel as a compact JSON STRING under
// `token_logprobs_json`; everywhere else in the app they are the parsed list under
// `token_logprobs`. The two names exist so nobody has to ask which representation
// they are holding. Written by `_prepare_workspace_body` in src/tinkerscope/pack.py,
// undone by `restore_logprobs` there — and by this, its mirror, for the static site
// where there is no backend to do it.
//
// Kept in its own dependency-free module (not in pack-install, which pulls in the api
// client) so it can be unit-tested under bare node like the other pure logic here.

/** Turn a pack body's `token_logprobs_json` strings back into inline `token_logprobs`,
 *  IN PLACE, so the caller's usual node split (lib/node-split) stores them as ordinary
 *  per-node blobs. An empty packed value drops the field rather than producing an empty
 *  list — the same falsy nuance as split_node, so a node can't advertise data it lacks. */
export function restoreLogprobs(body: Record<string, any>): Record<string, any> {
  for (const tree of Object.values(body?.trees ?? {}) as any[]) {
    for (const node of Object.values(tree?.nodes ?? {}) as any[]) {
      if (!node || typeof node !== 'object') continue;
      if (!('token_logprobs_json' in node)) continue;
      const packed = node.token_logprobs_json;
      delete node.token_logprobs_json;
      if (!packed) continue;
      try {
        node.token_logprobs = JSON.parse(packed);
      } catch (e) {
        throw new Error(`node ${node.id}: token_logprobs_json is not valid JSON (${e})`);
      }
    }
  }
  return body;
}
