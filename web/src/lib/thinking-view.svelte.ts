// Display toggle for how thinking/reasoning folds OPEN (sidebar "Thinking blocks").
//
// The folds default closed because an answer is usually what you came for; but a
// CoT study is the opposite — you are reading the reasoning, and re-clicking every
// fold across n samples × panels is the whole session. This is a browser viewing
// preference, not workspace state, so it lives in localStorage and never travels
// in a pack / export (a reader of a published site sets their own).
//
// Consumers read `.open` as the DEFAULT fold state; a fold the user then clicks
// keeps its own state until this flips again (flipping re-applies everywhere).

const KEY = 'tinkerscope:thinking-open';

class ThinkingViewStore {
  open = $state(false);

  constructor() {
    try {
      this.open = localStorage.getItem(KEY) === '1';
    } catch {
      /* SSR / storage disabled — default folded */
    }
  }

  set(on: boolean): void {
    this.open = on;
    try {
      localStorage.setItem(KEY, on ? '1' : '0');
    } catch {
      /* ignore */
    }
  }
}

export const thinkingView = new ThinkingViewStore();
