// Per-panel scroll policy — the ONLY place allowed to move a panel's scroll.
//
// History (why this exists): the app used to have a single $effect that pinned
// EVERY panel to its bottom whenever the shared state changed. Because every
// user action (branch cycle, thinking toggle, param edit, conversation load)
// round-trips POST /api/state → SSE patch → a wholesale live.state replacement,
// that effect yanked every panel to its bottom ~50 ms AFTER the DOM had already
// updated from the local tree — that async yank was the "scroll flicker", and
// its magnitude scaled with answer length (snap distance = scrollHeight −
// clientHeight). This store replaces "always pin" with three narrow policies:
//
//   1. FOLLOW — while a panel is streaming AND the user is stuck to its bottom,
//      keep it pinned (classic chat-log behavior). Scrolling up detaches;
//      scrolling back to the bottom re-attaches. Driven per-token by the
//      streaming $effect in +page.svelte.
//   2. PRESERVE — tree mutations (cycle/edit/regen/delete/select) capture the
//      panel's scrollTop and restore it after the DOM flush, so switching
//      branches never moves the view: a longer reply extends below the fold
//      (scroll down to read the end), a shorter one just ends sooner. Native
//      overflow-anchor isn't deterministic across row replacement — this is.
//      (If the new content is shorter than the viewport the browser clamps the
//      restored scrollTop; that's the desired "it's just shorter" case.)
//   3. SNAP — deliberate jumps to the latest turn: opening a conversation,
//      sending a message, receiving a branch from another panel. Also re-arms
//      FOLLOW's stick.
//   4. REVEAL — keyboard row-focus moved (↑/↓) to a row outside the viewport:
//      the minimal scroll that brings it in (scrollIntoView block:'nearest'
//      semantics, hand-rolled so ONLY this panel's container ever moves — the
//      native call also scrolls ancestors/the page). An already-visible row
//      doesn't move at all.
//
// Nothing else in the app may write scrollTop.

import { tick } from 'svelte';
import type { Panel } from './types';

/** Rounding/layout slack for "is the user at the bottom?". */
const STICK_SLOP_PX = 48;

class PanelScroll {
	/** The per-panel scroll containers (.messages), registered via `use:` below. */
	els: Record<Panel, HTMLDivElement | undefined> = $state({});
	/** Per-panel "stuck to bottom". Missing key = stuck, so new panels follow. */
	stick: Record<Panel, boolean> = $state({});

	#atBottom(el: HTMLElement): boolean {
		return el.scrollHeight - el.scrollTop - el.clientHeight < STICK_SLOP_PX;
	}

	/** Svelte action for a panel's `.messages` div: registers the element, opens
	 *  at the latest turn (fresh mounts start at scrollTop 0 = the oldest turn),
	 *  and tracks stickiness from scroll events. Programmatic scrolls also land
	 *  here — recomputing stick from them is idempotent and harmless. */
	register = (el: HTMLDivElement, panel: Panel) => {
		this.els[panel] = el;
		el.scrollTop = el.scrollHeight;
		const onScroll = () => (this.stick[panel] = this.#atBottom(el));
		el.addEventListener('scroll', onScroll, { passive: true });
		return {
			destroy: () => {
				el.removeEventListener('scroll', onScroll);
				if (this.els[panel] === el) delete this.els[panel];
			}
		};
	};

	/** FOLLOW: pin to bottom, only if stuck. Called per streamed token. */
	follow(panel: Panel) {
		const el = this.els[panel];
		if (el && this.stick[panel] !== false) el.scrollTop = el.scrollHeight;
	}

	/** PRESERVE: call right BEFORE a tree mutation; restores the current
	 *  scrollTop after the DOM flush (overriding native scroll anchoring). */
	preserve(panel: Panel) {
		const el = this.els[panel];
		if (!el) return;
		const top = el.scrollTop;
		void tick().then(() => {
			el.scrollTop = top;
		});
	}

	/** REVEAL: minimally scroll `el` (a row inside this panel's container) into
	 *  view — top-align when it's above the viewport, bottom-align when below,
	 *  no movement when already fully visible (block:'nearest' semantics). A row
	 *  taller than the viewport aligns its top. Synchronous: callers reveal on a
	 *  pure focus move (no DOM change pending). */
	reveal(panel: Panel, el: HTMLElement) {
		const c = this.els[panel];
		if (!c || !c.contains(el)) return;
		const pad = 8; // breathing room so the row isn't glued to the container edge
		const r = el.getBoundingClientRect();
		const top = r.top - c.getBoundingClientRect().top + c.scrollTop;
		const bottom = top + r.height;
		if (top - pad < c.scrollTop) c.scrollTop = Math.max(0, top - pad);
		else if (bottom + pad > c.scrollTop + c.clientHeight) c.scrollTop = bottom + pad - c.clientHeight;
	}

	/** SNAP: after the pending DOM flush, jump to the bottom + re-arm stick. */
	snap(panel: Panel) {
		this.stick[panel] = true;
		void tick().then(() => {
			const el = this.els[panel];
			if (el) el.scrollTop = el.scrollHeight;
		});
	}

	/** Snap every registered panel (conversation open / initial tree load). */
	async snapAll() {
		await tick();
		for (const [p, el] of Object.entries(this.els)) {
			if (!el) continue;
			this.stick[p] = true;
			el.scrollTop = el.scrollHeight;
		}
	}
}

export const panelScroll = new PanelScroll();
