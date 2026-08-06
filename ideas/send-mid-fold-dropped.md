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
