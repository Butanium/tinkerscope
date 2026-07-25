// Compact "when was this last touched" labels for list rows (the workspace
// picker). Relative while that reads better than a date, absolute after a week.

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/** `iso` = a tz-aware ISO timestamp (what the backend writes). Returns '' for
 *  anything unparseable so a caller can drop the line rather than show NaN. */
export function relWhen(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return '';
  const d = now - t;
  if (d < MIN) return 'just now';
  if (d < HOUR) return `${Math.floor(d / MIN)}m ago`;
  if (d < DAY) return `${Math.floor(d / HOUR)}h ago`;
  if (d < 7 * DAY) return `${Math.floor(d / DAY)}d ago`;
  const dt = new Date(t);
  const sameYear = dt.getFullYear() === new Date(now).getFullYear();
  return dt.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' })
  });
}
