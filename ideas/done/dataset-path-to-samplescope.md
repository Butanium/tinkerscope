## The dataset path wants to be a samplescope command

⇧+copy on a panel's copy button now hands out the absolute path of the run's
training JSONL (shipped 2026-08-06, e98ed55). Clément's stated reason for wanting
it: *"so that you can put it in a dataset viewer."* On this box that viewer is
**samplescope** — `sscope view <path>` opens a JSONL in the browser, and there's a
skill for driving it.

So the copied string is one step short of the thing actually wanted. Options, in
increasing ambition:

1. **⇧⌥ copies `sscope view <path>` instead of the bare path.** Zero coupling —
   it's a string. But it bakes one tool's CLI into tinkerscope, and a collaborator
   who doesn't have samplescope gets a command that doesn't exist.
2. **A per-run "open training data" action that shells out.** Real coupling, and
   tinkerscope has no business spawning other people's servers.
3. **Do nothing.** The path is copyable; pasting it after `sscope view` is one
   keystroke of work.

(3) is probably right and this note exists mainly to record the *observation*: the
two tools sit next to each other in the same workflow (look at what a checkpoint
says → look at what it was trained on) and nothing connects them. If a future
session finds itself repeatedly copying a path out of tinkerscope and into
samplescope, that friction is the signal to revisit — and (1) is the cheap version.

Worth noting the reverse direction too: samplescope has no idea a JSONL it's
showing trained a checkpoint you could go sample. A shared notion of "this run
↔ this dataset" would serve both, and neither has it.

*(opus-5, 2026-08-06, peek-deletion session)*

**Done 2026-08-06** (`4fc5091`): Clément wanted it, and the answer was better than
any of the three above — samplescope already had a deep link (`?path=`) and an
instance registry, so no `sscope view` string-building was needed at all.
Ctrl+⇧ on the copy button → `POST /api/samplescope/open {run_id}` → a URL, opened
in a new tab, starting a viewer when none serves the file. The "reverse direction"
note stands: samplescope still has no idea a JSONL it's showing trained a
checkpoint you could go sample.
