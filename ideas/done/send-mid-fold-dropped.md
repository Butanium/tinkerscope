## A send fired mid-fold is silently dropped

During the README shoot, the second composer send of a 2-turn compare fired right
after the first turn's stop-chips cleared and was swallowed whole — no error, no
user row, text left in the textarea (the shot shipped with a one-turn
conversation before the retry landed).

The shots script now retries until the user rows appear
(`browser_readme_shots.py`, 04513f7's note), but the app behavior is the real
bug: a send the UI accepts should queue behind the fold or visibly refuse, never
vanish.

Repro shape: `send → wait for stop-chip absence → immediately send again`.

*(fable-5, 2026-08-05)*

**Done 2026-08-12**: closed structurally by P2 server-authored folds
(p2-folds + p2-adopt). Mechanism of the drop, finally pinned: the swallowed
send appended its user turn at the PRE-fold active leaf (the previous user
node); when the fold then landed and selected its own first sibling, the new
turn was left hanging off-path — present in the tree, invisible in the view.
Under P2 the fold's ops event is broadcast BEFORE `running` clears (contract
ordering), so by the time the stop chip disappears and a send can fire, the
tree already holds the fold and the new turn lands under the selected
assistant. Pinned by browser_send_adopt.py's rapid-second-send scenario.
