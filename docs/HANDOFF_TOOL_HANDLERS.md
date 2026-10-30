# Handoff — executable tool handlers (written 2026-10-30, not started)

Clément, verbatim: "I don't want to have to type the confirmation at every call of send
back to chat … able to both change the tools and what they do from the UI with custom
python code (and make sure the CLI also is nicely integrating this)". Requested via the
research-hq dispatcher (team-lead) for the Terra study (`~/projects2/village-terra`, its
tinkerscope = :8770, tmux `run/terra-tscope`, window `tscope`).

## What exists today (read first)

- **Tool use, chat wire** — `docs/API_CONTRACT.md` "Tool use"; `api/openrouter.py`;
  `tree.ts` `pendingToolCalls` / `appendToolResults` / `editToolResult` / `isFireParent`;
  `lib/ToolResultForm.svelte` (the per-panel manual answer form), `lib/ToolsModal.svelte`
  + `lib/tools-def.ts` (workspace `tools` editor, verbatim storage, `toolName`);
  `branchOps.answerToolCalls`. Tool results are `role:"tool"` nodes chained under the
  calling assistant turn; the next model turn folds under the last result.
- **Responses wire** — `api/openrouter_responses.py` (module docstring = the contract,
  incl. probe findings), `ENGINEERING_LOGS.md` 2026-10-30. Verbatim `items` blobs; a
  workspace imported with `tinkerscope import --responses-call` is `wire.exact`. A
  computer call is answered with a user IMAGE message (OpenRouter has no
  `computer_call_output`).
- **CLI** — `continue --tool-result` (`@image:PATH` for computer calls), `replay`,
  `continue --no-persist`, `--tools-file`, `import`/`export`. Server-side folds:
  `routes/chat.py` `_build_fold_nodes` (a chat with `parent_node` folds all n samples).

## The spec (team-lead's, adjust with judgment, log decisions in ENGINEERING_LOGS)

- Each workspace tool may carry a HANDLER next to its schema: `manual` (today), `static`
  (fixed result text, e.g. `send_message_to_chat` → "Message successfully sent back to
  chat"), `python` (a function body getting `name`, `args` (parsed JSON), `call_id`, and a
  context: workspace id, panel, the thread so far; returns a string, later `{text, image}`
  for computer-style results).
- When a sampled turn calls tools that ALL have handlers, the server runs them, folds the
  tool nodes, and auto-continues sampling, up to a per-workspace max-steps (default ~5).
  Stop at the first `manual` tool, or when a turn makes no calls. Per-tool
  `auto_continue` (default on): off = run + fold the result, don't fire the next turn.
  n>1: each sample's branch runs its own loop.
- UI: the tools modal gets a per-tool handler editor (mode switch, static text, Python
  box with a "test run" on sample args). Tool nodes made by a handler show an
  "auto-answered" badge. `?` modal + guide skill document it (repo rule: UI change ships
  with HelpModal + guide skill; CLI change with cli skill + `_gen_cli_ref`).
- CLI: `tinkerscope tools --ws X` (list), `tools set --ws X NAME [--schema-file F]
  [--static TEXT | --python-file F | --manual] [--no-auto-continue]`, `tools test --ws X
  NAME --args JSON`. `send`/`continue`/`replay` run handlers by default; `--no-auto-tools`,
  `--max-tool-steps`. `replay --no-persist` can run handlers too.
- SECURITY: python handlers = arbitrary code execution, and :8770 is public via ngrok
  (basic auth). OFF unless the server starts with an explicit flag (e.g.
  `--allow-tool-code`); run handlers in a SUBPROCESS with a timeout, never in-process;
  static/manual always work; the UI says when python handlers are disabled.
- Reusable named tool sets (e.g. "AI Village scaffold") across workspaces; keep
  `import --responses-call` working with them.
- When done: merge, restart :8770 with the code flag added to
  `~/projects2/village-terra/run_tinkerscope.sh` (Clément wants it there), report to
  team-lead (SendMessage `team-lead`).

## Design notes I'd start from (mine, not decided)

- Where the loop lives: server-side, in the chat terminal after the fold (it has the
  folded node ids + workspace). A loop step = run handlers → `add_nodes` tool chain →
  fire a new chat with `parent_node` = last tool node, same params. Bound by
  max-steps; every step is its own chat (own chat_id, bus events) so the browser
  renders it like any fire.
- Handler storage: on the tool def itself would break verbatim tool storage (the tool
  list is sent to the model as-is) — keep handlers in a SEPARATE workspace meta map
  (`tool_handlers: {name: {mode, text?, code?, auto_continue}}`), stripped of nothing.
- Responses wire: a function handler's result → `function_call_output`; a computer
  handler `{image}` → the user image message shape (see above).

## Rules that bit before (read `CLAUDE.md` + these)

- Test on an ISOLATED instance (`scripts/dev-isolated.sh --port N --fresh <dir>`); check
  the port is free AND that `/api/health`'s `root` is yours (memory
  `isolated-port-squatters`). Never fire at the Terra workspaces ("Terra — forks",
  "Terra — Claude's night notebook") — synthetic tools/prompts only (a forked Terra said
  it doesn't consent to copies). Never test against :8767.
- Work in a worktree off local main with its own `npm install`; `merge --ff-only` skips
  the build hook → `npm run build` in main after. Never `git add -A`.
- Restarting :8770: kill the pid on :8770 → the `tscope` window closes → recreate it:
  `tmux new-window -d -t 'run/terra-tscope:0' -n tscope -c ~/projects2/village-terra
  './run_tinkerscope.sh'`, then verify new pid + `/api/health`.
