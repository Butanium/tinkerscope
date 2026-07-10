// Display toggle for the per-token logprob inspector (sidebar "Token probs").
//
// DISPLAY-only: capture is always on for native tinker sampling (the server
// default; see docs/API_CONTRACT.md `logprobs`), so flipping this on works
// retroactively on any turn that already carries token_logprobs. Persisted in
// localStorage — it's a browser viewing preference, not conversation state.

const KEY = 'tinkerscope:token-probs';

class LogprobViewStore {
	enabled = $state(false);

	constructor() {
		try {
			this.enabled = localStorage.getItem(KEY) === '1';
		} catch {
			/* SSR / storage disabled — default off */
		}
	}

	set(on: boolean): void {
		this.enabled = on;
		try {
			localStorage.setItem(KEY, on ? '1' : '0');
		} catch {
			/* ignore */
		}
	}
}

export const logprobView = new LogprobViewStore();
