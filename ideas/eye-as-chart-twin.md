## Sample-view (the eye) as the chart's in-thread twin

Two natural extensions of 23c3372's think/no-think filter:

(a) filter the cards by HIGHLIGHT-RULE match — the chart already buckets by rule,
so chips like the chart's include/exclude row would make the eye "the chart's
buckets, but readable in place";

(b) clicking a chart bar segment could jump to the eye view of that turn with the
segment's bucket as the active filter, instead of (or alongside) the modal's
inspector list.

Both reuse `highlight-match` + `expandTurnSamples`; the filter seam in
ChatMessage (`showSample`) is where a rule predicate would slot in.

*(fable, 2026-08-05, while shipping the eye)*
