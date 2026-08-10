// The search palette's jump-and-reveal beacon: which node was just revealed, so
// the row can flash and (for a thinking-field hit) open its reasoning fold.
// ChatMessage reads it by node id; +page writes it after selectPathTo + scroll.
// Self-clearing — the flash is a moment, not a mode.

class RevealStore {
  target = $state<{ panel: string; nodeId: string; field: string } | null>(null);
  #timer: ReturnType<typeof setTimeout> | undefined;

  show(panel: string, nodeId: string, field: string): void {
    clearTimeout(this.#timer);
    this.target = { panel, nodeId, field };
    this.#timer = setTimeout(() => (this.target = null), 2600);
  }
}

export const reveal = new RevealStore();
